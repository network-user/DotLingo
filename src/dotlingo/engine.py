from __future__ import annotations

import multiprocessing as mp
import os
import queue
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable


class InferenceError(RuntimeError):
    pass


class InferenceUnavailable(InferenceError):
    pass


class InferenceCancelled(InferenceError):
    pass


class InferenceTimeout(InferenceError):
    pass


def model_runtime_options(model: dict[str, Any]) -> dict[str, Any]:
    """Стоп-строки и флаги шаблона. Документ и диалог берут один и тот же набор."""
    profile = str(model.get("prompt_profile") or "")
    prompt_style = str(model.get("prompt_style") or "general")
    qwen_style = model.get("append_no_think")
    if qwen_style is None:
        # Свой файл без явного флага не получает /no_think: строка попала бы в текст.
        # Блок рассуждения всё равно закрывается пустым <think>, как у перевода документа.
        qwen_style = not model.get("custom")
    if profile == "gemma":
        return {
            "stop_sequences": ("<end_of_turn>",),
            "append_no_think": False,
            "user_only": True,
            "plain_gemma_turns": True,
            "close_think": False,
        }
    if prompt_style == "hy-mt2":
        return {
            "stop_sequences": None,
            "append_no_think": bool(model.get("append_no_think")),
            "user_only": True,
            "plain_gemma_turns": False,
            "close_think": False,
        }
    if qwen_style:
        return {
            "stop_sequences": ("<|im_end|>", "<|fim_suffix|>"),
            "append_no_think": True,
            "user_only": False,
            "plain_gemma_turns": False,
            "close_think": True,
        }
    return {
        "stop_sequences": None,
        "append_no_think": False,
        "user_only": False,
        "plain_gemma_turns": False,
        "close_think": True,
    }


def prepare_llama_env() -> None:
    """Подготовить загрузку llama.cpp в этом процессе.

    Битый CUDA_PATH убирается, чтобы CPU-колесо не падало на пустом каталоге.
    Каталоги pip-пакетов nvidia/*/bin регистрируются до импорта: CUDA-колесо
    ищет cublas рядом с собой, а не только в Toolkit.
    """
    _drop_stale_cuda_path()
    _register_nvidia_pip_dlls()


def _drop_stale_cuda_path() -> None:
    cuda = os.environ.get("CUDA_PATH")
    if not cuda:
        return
    root = Path(cuda)
    bin_dir = root if root.name.lower() == "bin" else root / "bin"
    if bin_dir.is_dir():
        return
    os.environ.pop("CUDA_PATH", None)
    stale = {os.path.normcase(str(bin_dir)), os.path.normcase(str(root))}
    path = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join(
        item for item in path.split(os.pathsep) if item and os.path.normcase(item) not in stale
    )


# Каталоги внутри site-packages/nvidia у колёс nvidia-cublas-cu12 и nvidia-cuda-runtime-cu12.
_NVIDIA_RUNTIME_DIRS = frozenset({"cublas", "cuda_runtime"})


def _register_nvidia_pip_dlls() -> None:
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    seen: set[str] = set()
    for entry in sys.path:
        if not entry:
            continue
        root = Path(entry)
        if root.name not in {"site-packages", "dist-packages"}:
            continue
        nvidia = root / "nvidia"
        if not nvidia.is_dir():
            continue
        try:
            children = list(nvidia.iterdir())
        except OSError:
            continue
        for child in children:
            if child.name not in _NVIDIA_RUNTIME_DIRS:
                continue
            for folder in ("bin", "lib"):
                path = child / folder
                if not path.is_dir():
                    continue
                key = os.path.normcase(str(path))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    os.add_dll_directory(str(path))
                except OSError:
                    continue
                current = os.environ.get("PATH", "")
                if key not in os.path.normcase(current):
                    os.environ["PATH"] = str(path) + os.pathsep + current


# Plain Gemma turns. TranslateGemma's embedded jinja rejects a string user message
# unless source_lang_code is a structured field, which this chat API does not send.
GEMMA_TURN_TEMPLATE = (
    "{{ bos_token }}"
    "{%- for message in messages %}"
    "{%- if message['role'] == 'user' %}"
    "{{ '<start_of_turn>user\\n' + message['content'] + '<end_of_turn>\\n' }}"
    "{%- elif message['role'] == 'assistant' %}"
    "{{ '<start_of_turn>model\\n' + message['content'] + '<end_of_turn>\\n' }}"
    "{%- endif %}"
    "{%- endfor %}"
    "{%- if add_generation_prompt %}"
    "{{ '<start_of_turn>model\\n' }}"
    "{%- endif %}"
)


def render_gemma_turns(
    messages: list[dict[str, str]],
    *,
    bos_token: str,
    eos_token: str,
) -> str:
    from llama_cpp.llama_chat_format import Jinja2ChatFormatter

    formatter = Jinja2ChatFormatter(
        template=GEMMA_TURN_TEMPLATE,
        eos_token=eos_token,
        bos_token=bos_token,
    )
    return str(formatter(messages=messages).prompt)


def install_gemma_turn_handler(model: Any) -> None:
    """Use plain Gemma turns instead of the embedded TranslateGemma template."""
    from llama_cpp.llama_chat_format import Jinja2ChatFormatter

    eos_id = int(model.token_eos())
    bos_id = int(model.token_bos())
    end_id = _special_token_id(model, "<end_of_turn>")
    stop_ids = [token_id for token_id in (eos_id, end_id) if token_id >= 0]
    model.chat_handler = Jinja2ChatFormatter(
        template=GEMMA_TURN_TEMPLATE,
        eos_token=_token_piece(model, eos_id) or "<end_of_turn>",
        bos_token=_token_piece(model, bos_id),
        stop_token_ids=stop_ids or None,
    ).to_chat_handler()


# Qwen3 думает, пока в шаблон не передан enable_thinking=false.
# create_chat_completion этот аргумент не прокидывает, поэтому перевод
# подставляет пустой блок и модель сразу пишет ответ.
_THINKING_SWITCH = re.compile(
    r"\{%-?\s*if enable_thinking is defined and enable_thinking is false\s*-?%\}"
    r".*?"
    r"\{%-?\s*endif\s*-?%\}",
    re.DOTALL,
)
_EMPTY_THINK = "{{- '<think>\\n\\n</think>\\n\\n' }}"


def force_closed_think(template: str) -> str | None:
    """Вернуть шаблон, который начинает ответ после пустого блока размышления."""
    if "enable_thinking" not in template:
        return None
    updated, count = _THINKING_SWITCH.subn(_EMPTY_THINK, template, count=1)
    if count != 1:
        return None
    return updated


def install_closed_think_handler(model: Any) -> bool:
    """Закрыть размышление Qwen3. Для шаблона без этого переключателя ничего не меняет."""
    metadata = getattr(model, "metadata", None) or {}
    forced = force_closed_think(str(metadata.get("tokenizer.chat_template") or ""))
    if forced is None:
        return False
    from llama_cpp.llama_chat_format import Jinja2ChatFormatter

    eos_id = int(model.token_eos())
    bos_id = int(model.token_bos())
    im_end = _special_token_id(model, "<|im_end|>")
    stop_ids = [token_id for token_id in (eos_id, im_end) if token_id >= 0]
    model.chat_handler = Jinja2ChatFormatter(
        template=forced,
        eos_token=_token_piece(model, eos_id) or "<|im_end|>",
        bos_token=_token_piece(model, bos_id),
        stop_token_ids=stop_ids or None,
    ).to_chat_handler()
    return True


def _token_piece(model: Any, token_id: int) -> str:
    if token_id < 0:
        return ""
    raw = model.detokenize([token_id], special=True)
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _special_token_id(model: Any, text: str) -> int:
    try:
        pieces = model.tokenize(text.encode("utf-8"), add_bos=False, special=True)
    except Exception:
        return -1
    if len(pieces) == 1:
        return int(pieces[0])
    return -1


def _worker_main(
    model_path: str,
    context_size: int,
    threads: int,
    gpu_layers: int,
    sampling: dict[str, Any],
    requests: Any,
    responses: Any,
    paused: Any,
    cancelled: Any,
    stop_sequences: tuple[str, ...] | None,
    append_no_think: bool,
    user_only: bool,
    plain_gemma_turns: bool = False,
    n_batch: int = 512,
    close_think: bool = False,
) -> None:
    """Load untrusted data weights in a child process and expose only text requests."""
    prepare_llama_env()
    try:
        from llama_cpp import Llama

        model = Llama(
            model_path=model_path,
            n_ctx=context_size,
            n_batch=max(64, min(int(n_batch), context_size)),
            n_threads=max(1, threads),
            n_gpu_layers=gpu_layers,
            verbose=False,
        )
        if plain_gemma_turns:
            install_gemma_turn_handler(model)
        elif close_think and install_closed_think_handler(model):
            # Строка /no_think этому шаблону не нужна и попала бы в текст пользователя.
            append_no_think = False
            stops = list(stop_sequences or ())
            for token in ("<|im_end|>", "<|fim_suffix|>"):
                if token not in stops:
                    stops.append(token)
            stop_sequences = tuple(stops)
        responses.put({"type": "ready"})
    except Exception as exc:
        responses.put({"type": "startup_error", "message": _safe_backend_error(exc)})
        return

    temperature = float(sampling.get("temperature", 0.15))
    top_p = float(sampling.get("top_p", 0.85))
    top_k = int(sampling.get("top_k", 40))
    repeat_penalty = float(sampling.get("repeat_penalty", 1.05))

    while True:
        request = requests.get()
        if request is None:
            return
        try:
            user = request["user"] + ("\n/no_think" if append_no_think else "")
            messages: list[dict[str, str]] = []
            system = str(request.get("system") or "")
            if system.strip() and not user_only:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": user})
            chunks: list[str] = []
            stream = model.create_chat_completion(
                messages=messages,
                stream=True,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                repeat_penalty=repeat_penalty,
                max_tokens=request["max_tokens"],
                stop=list(stop_sequences) if stop_sequences else None,
            )
            for item in stream:
                while not paused.is_set():
                    if cancelled.is_set():
                        raise InferenceCancelled("Задача отменена.")
                    time.sleep(0.1)
                if cancelled.is_set():
                    raise InferenceCancelled("Задача отменена.")
                text = item.get("choices", [{}])[0].get("delta", {}).get("content", "") or ""
                if text:
                    chunks.append(text)
                    responses.put({"type": "token", "request_id": request["id"], "text": text})
            result = _strip_reasoning("".join(chunks)).strip()
            if not result:
                raise InferenceError("Модель вернула пустой ответ. Фрагмент можно повторить.")
            responses.put({"type": "complete", "request_id": request["id"], "text": result})
        except InferenceCancelled:
            responses.put({"type": "cancelled", "request_id": request["id"]})
            return
        except Exception as exc:
            responses.put(
                {"type": "request_error", "request_id": request["id"], "message": _safe_backend_error(exc)}
            )


def _strip_reasoning(text: str) -> str:
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    if "<think>" in text and "</think>" not in text:
        return ""
    text = re.sub(r"<\|(?:im_end|fim_suffix|endoftext)\|>", "", text)
    text = text.replace("<end_of_turn>", "").replace("<start_of_turn>", "")
    return text.strip()


def _safe_backend_error(exc: Exception) -> str:
    text = str(exc).lower()
    if any(word in text for word in ("out of memory", "bad_alloc", "cuda_error_out_of_memory", "not enough memory")):
        return "Недостаточно RAM или VRAM для выбранного контекста. Уменьшите контекст или выберите меньшую модель."
    if any(word in text for word in ("illegal instruction", "winerror 193")):
        return (
            "Эта сборка llama.cpp не запускается на этом процессоре. "
            "В настройках устройства выберите сборку «Процессор»."
        )
    if "failed to load shared library" in text or "winerror 127" in text or "не найдена указанная процедура" in text:
        return (
            "llama-cpp-python установлен, но его DLL не совпала с библиотеками на компьютере. "
            "Для этого приложения подходит CPU-сборка llama-cpp-python==0.3.35."
        )
    if "cuda" in text and any(word in text for word in ("winerror", "not find", "no such file", "не удается найти", "dll")):
        return (
            "Сборка llama-cpp-python ищет CUDA Toolkit, а этот путь на компьютере не найден. "
            "Нужна CPU-сборка runtime или установленный CUDA той же версии."
        )
    if any(word in text for word in ("failed to load", "invalid gguf", "unknown model", "unsupported model")):
        return "Backend не смог открыть проверенный вес GGUF. Проверьте версию runtime и модель."
    if "llama_cpp" in str(exc) or "no module named" in text:
        return "Локальный inference-runtime не установлен. Установите llama-cpp-python для этого приложения."
    return "Ошибка локальной модели. Повторите фрагмент; предыдущие завершённые фрагменты сохранены."


def _memory_failure(message: str) -> bool:
    text = message.lower()
    return any(word in text for word in ("памят", "memory", "vram", "cuda", "bad_alloc"))


class InferenceProcess:
    """Persistent child process for one document; controls never touch UI objects."""

    def __init__(
        self,
        model_path: Path,
        context_size: int,
        threads: int,
        *,
        gpu_layers: int = 0,
        sampling: dict[str, Any] | None = None,
        stop_sequences: tuple[str, ...] | None = None,
        append_no_think: bool = False,
        user_only: bool = False,
        plain_gemma_turns: bool = False,
        close_think: bool = False,
        n_batch: int = 512,
        startup_timeout: float = 180,
        idle_timeout: float = 240,
    ) -> None:
        self.model_path = Path(model_path)
        self.context_size = context_size
        self.threads = threads
        self.gpu_layers = int(gpu_layers)
        self.sampling = sampling or {
            "temperature": 0.15,
            "top_p": 0.85,
            "top_k": 40,
            "repeat_penalty": 1.05,
        }
        self.stop_sequences = stop_sequences
        self.append_no_think = append_no_think
        self.user_only = user_only
        self.plain_gemma_turns = plain_gemma_turns
        self.close_think = close_think
        self.n_batch = max(64, min(int(n_batch), self.context_size))
        self.fell_back_to_cpu = False
        self.startup_timeout = startup_timeout
        self.idle_timeout = idle_timeout
        self._ctx = mp.get_context("spawn")
        self._requests = self._ctx.Queue()
        self._responses = self._ctx.Queue()
        self._paused = self._ctx.Event()
        self._paused.set()
        self._cancelled = self._ctx.Event()
        self._process: Any = None
        self._started = False
        self._request_id = 0

    def _launch(self) -> dict[str, Any]:
        if self._cancelled.is_set():
            return {"type": "cancelled", "message": "Задача отменена."}
        self._process = self._ctx.Process(
            target=_worker_main,
            args=(
                str(self.model_path),
                self.context_size,
                self.threads,
                self.gpu_layers,
                self.sampling,
                self._requests,
                self._responses,
                self._paused,
                self._cancelled,
                self.stop_sequences,
                self.append_no_think,
                self.user_only,
                self.plain_gemma_turns,
                self.n_batch,
                self.close_think,
            ),
            name="DotLingo inference",
            daemon=True,
        )
        self._process.start()
        self._started = True
        return self._wait_for_startup()

    def _wait_for_startup(self) -> dict[str, Any]:
        """Ждать готовность короткими шагами, чтобы «Стоп» оборвал загрузку GGUF."""
        deadline = time.monotonic() + self.startup_timeout
        while True:
            if self._cancelled.is_set():
                self.terminate()
                return {"type": "cancelled", "message": "Задача отменена."}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.terminate()
                minutes = max(1, round(self.startup_timeout / 60))
                return {
                    "type": "startup_timeout",
                    "message": f"Backend не сообщил о готовности за {minutes} мин.",
                }
            try:
                event = self._responses.get(timeout=min(0.2, remaining))
            except queue.Empty:
                process = self._process
                if self._started and (process is None or not process.is_alive()):
                    self.terminate()
                    return {
                        "type": "startup_error",
                        "message": "Процесс модели завершился до готовности.",
                    }
                continue
            if self._cancelled.is_set():
                self.terminate()
                return {"type": "cancelled", "message": "Задача отменена."}
            if isinstance(event, dict):
                return event
            return {"type": "startup_error", "message": "Пустой ответ backend."}

    def start(self) -> None:
        if self._cancelled.is_set():
            raise InferenceCancelled("Задача отменена.")
        if self._started and self._process is not None and self._process.is_alive():
            return
        if not self.model_path.is_file():
            raise InferenceUnavailable("Файл модели не найден. Установите выбранную модель на странице «Модели».")
        try:
            with self.model_path.open("rb") as model:
                if model.read(4) != b"GGUF":
                    raise InferenceUnavailable("Файл не прошёл проверку формата GGUF.")
        except OSError as exc:
            raise InferenceUnavailable("Не удалось прочитать файл модели.") from exc
        event = self._launch()
        if event.get("type") == "cancelled":
            self.terminate()
            raise InferenceCancelled("Задача отменена.")
        if event.get("type") != "ready" and self.gpu_layers != 0 and _memory_failure(str(event.get("message", ""))):
            self.terminate()
            self._cancelled.clear()
            self._paused.set()
            # Длинное окно и крупный батч на CPU раздувают RAM. Цифры совпадают
            # с hardware.CPU_CONTEXT_CAP и hardware.CPU_BATCH.
            from dotlingo.hardware import CPU_BATCH, CPU_CONTEXT_CAP

            self.gpu_layers = 0
            self.fell_back_to_cpu = True
            self.context_size = min(self.context_size, CPU_CONTEXT_CAP)
            self.n_batch = max(64, min(CPU_BATCH, self.context_size))
            event = self._launch()
        if event.get("type") != "ready":
            message = str(event.get("message", "Не удалось запустить inference backend."))
            self.terminate()
            if event.get("type") == "startup_timeout":
                raise InferenceTimeout(message)
            raise InferenceUnavailable(message)

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    def cancel(self) -> None:
        self._cancelled.set()
        self._paused.set()

    def translate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1800,
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        self.start()
        self._request_id += 1
        request_id = self._request_id
        self._requests.put({"id": request_id, "system": system, "user": user, "max_tokens": max_tokens})
        tokens: list[str] = []
        last_event = time.monotonic()
        while True:
            if self._cancelled.is_set():
                self.terminate()
                raise InferenceCancelled("Задача отменена.")
            try:
                event = self._responses.get(timeout=0.2)
            except queue.Empty:
                if self._process is None or not self._process.is_alive():
                    raise InferenceError("Процесс inference завершился без результата.")
                if not self._paused.is_set():
                    last_event = time.monotonic()
                    continue
                if time.monotonic() - last_event > self.idle_timeout:
                    self.terminate()
                    minutes = max(1, round(self.idle_timeout / 60))
                    raise InferenceTimeout(
                        f"Backend не сообщал о генерации {minutes} мин. и был остановлен."
                    )
                continue
            if event.get("request_id") != request_id:
                continue
            last_event = time.monotonic()
            kind = event.get("type")
            if kind == "token":
                text = event.get("text", "")
                tokens.append(text)
                if on_token:
                    on_token(text)
            elif kind == "complete":
                return event["text"]
            elif kind == "cancelled":
                raise InferenceCancelled("Задача отменена.")
            elif kind == "request_error":
                raise InferenceError(event.get("message", "Ошибка локальной модели."))

    def terminate(self) -> None:
        process = self._process
        if self._started and process is not None and process.is_alive():
            self._cancelled.set()
            process.terminate()
            process.join(timeout=4)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        self._started = False

    def close(self) -> None:
        process = self._process
        if process is not None and process.is_alive() and not self._cancelled.is_set():
            self._requests.put(None)
            process.join(timeout=3)
        self.terminate()
        for channel in (self._requests, self._responses):
            try:
                channel.close()
            except (OSError, ValueError):
                pass

    def __enter__(self) -> InferenceProcess:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
