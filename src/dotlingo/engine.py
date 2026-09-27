from __future__ import annotations

import multiprocessing as mp
import queue
import re
import threading
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


def _worker_main(
    model_path: str,
    context_size: int,
    threads: int,
    gpu_layers: int,
    requests: Any,
    responses: Any,
    paused: Any,
    cancelled: Any,
    stop_sequences: tuple[str, ...] | None,
    append_no_think: bool,
) -> None:
    """Load untrusted data weights in a child process and expose only text requests."""
    try:
        from llama_cpp import Llama

        model = Llama(
            model_path=model_path,
            n_ctx=context_size,
            n_threads=max(1, threads),
            n_gpu_layers=gpu_layers,
            verbose=False,
        )
        responses.put({"type": "ready"})
    except Exception as exc:
        responses.put({"type": "startup_error", "message": _safe_backend_error(exc)})
        return

    while True:
        request = requests.get()
        if request is None:
            return
        try:
            messages = [
                {"role": "system", "content": request["system"]},
                {
                    "role": "user",
                    "content": request["user"] + ("\n/no_think" if append_no_think else ""),
                },
            ]
            chunks: list[str] = []
            stream = model.create_chat_completion(
                messages=messages,
                stream=True,
                temperature=0.15,
                top_p=0.85,
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
    return re.sub(r"<\|(?:im_end|fim_suffix|endoftext)\|>", "", text).strip()


def _safe_backend_error(exc: Exception) -> str:
    text = str(exc).lower()
    if any(word in text for word in ("out of memory", "bad_alloc", "cuda_error_out_of_memory", "not enough memory")):
        return "Недостаточно RAM или VRAM для выбранного контекста. Уменьшите контекст или выберите меньшую модель."
    if any(word in text for word in ("failed to load", "invalid gguf", "unknown model", "unsupported model")):
        return "Backend не смог открыть проверенный вес GGUF. Проверьте версию runtime и модель."
    if "llama_cpp" in str(exc) or "no module named" in text:
        return "Локальный inference-runtime не установлен. Установите llama-cpp-python для этого приложения."
    return "Ошибка локальной модели. Повторите фрагмент; предыдущие завершённые фрагменты сохранены."


class InferenceProcess:
    """Persistent child process for one document; controls never touch UI objects."""

    def __init__(
        self,
        model_path: Path,
        context_size: int,
        threads: int,
        *,
        use_gpu: bool = False,
        stop_sequences: tuple[str, ...] | None = ("<|im_end|>", "<|fim_suffix|>"),
        append_no_think: bool = True,
        startup_timeout: float = 180,
        idle_timeout: float = 240,
    ) -> None:
        self.model_path = Path(model_path)
        self.context_size = context_size
        self.threads = threads
        self.gpu_layers = -1 if use_gpu else 0
        self.startup_timeout = startup_timeout
        self.idle_timeout = idle_timeout
        self._ctx = mp.get_context("spawn")
        self._requests = self._ctx.Queue()
        self._responses = self._ctx.Queue()
        self._paused = self._ctx.Event()
        self._paused.set()
        self._cancelled = self._ctx.Event()
        self._process = self._ctx.Process(
            target=_worker_main,
            args=(
                str(self.model_path), context_size, threads, self.gpu_layers,
                self._requests, self._responses, self._paused, self._cancelled,
                stop_sequences, append_no_think,
            ),
            name="DotLingo inference",
            daemon=True,
        )
        self._started = False
        self._request_id = 0
        self._write_lock = threading.Lock()

    def start(self) -> None:
        if self._started:
            return
        if not self.model_path.is_file():
            raise InferenceUnavailable("Файл модели не найден. Установите выбранную модель на странице «Модели».")
        try:
            with self.model_path.open("rb") as model:
                if model.read(4) != b"GGUF":
                    raise InferenceUnavailable("Файл не прошёл проверку формата GGUF.")
        except OSError as exc:
            raise InferenceUnavailable("Не удалось прочитать файл модели.") from exc
        self._process.start()
        self._started = True
        try:
            event = self._responses.get(timeout=self.startup_timeout)
        except queue.Empty as exc:
            self.terminate()
            raise InferenceTimeout("Backend не сообщил о готовности за 3 минуты.") from exc
        if event.get("type") != "ready":
            self.terminate()
            raise InferenceUnavailable(event.get("message", "Не удалось запустить inference backend."))

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
                if not self._process.is_alive():
                    raise InferenceError("Процесс inference завершился без результата.")
                if time.monotonic() - last_event > self.idle_timeout:
                    self.terminate()
                    raise InferenceTimeout("Backend не сообщал о генерации 4 минуты и был остановлен.")
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
        if self._started and self._process.is_alive():
            self._cancelled.set()
            self._process.terminate()
            self._process.join(timeout=4)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=2)
        self._started = False

    def close(self) -> None:
        if self._process.is_alive() and not self._cancelled.is_set():
            self._requests.put(None)
            self._process.join(timeout=3)
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
