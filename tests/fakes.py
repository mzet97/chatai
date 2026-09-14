"""SDK simulado compatível com anthropic==1.5.0 (sem monkeypatch de APIs internas).

Os serviços recebem o cliente por injeção (`client=`); aqui um falso com o mesmo
formato de uso: `messages.count_tokens`, `messages.stream` (CM async com
`text_stream` + `get_final_message`), `messages.create`, `models.list` paginado.
"""

from types import SimpleNamespace


class FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeFinalMessage:
    def __init__(
        self,
        text,
        *,
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
        model="fake-model",
        msg_id="msg_fake",
    ):
        self.content = [FakeTextBlock(text)]
        self.stop_reason = stop_reason
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        self.model = model
        self.id = msg_id


class FakeStream:
    def __init__(self, deltas, final, *, request_id="req_fake", mid_error=None):
        self._deltas = list(deltas)
        self._final = final
        self.request_id = request_id
        self._mid_error = mid_error
        self._override = None
        self.closed = False

    @property
    def text_stream(self):
        return self._override if self._override is not None else self._gen()

    @text_stream.setter
    def text_stream(self, gen):
        self._override = gen

    async def _gen(self):
        for i, d in enumerate(self._deltas):
            if self._mid_error is not None and i == len(self._deltas) - 1:
                raise self._mid_error
            yield d

    async def get_final_message(self):
        return self._final

    async def close(self):
        self.closed = True


class FakeStreamManager:
    def __init__(self, stream=None, enter_error=None):
        self._stream = stream
        self._enter_error = enter_error

    async def __aenter__(self):
        if self._enter_error is not None:
            raise self._enter_error
        return self._stream

    async def __aexit__(self, *exc):
        return False


class FakeMessagesNamespace:
    def __init__(
        self,
        *,
        count_value=100,
        count_error=None,
        stream_manager=None,
        create_result=None,
        create_error=None,
    ):
        self.calls = {"count_tokens": [], "stream": [], "create": []}
        self._count_value = count_value
        self._count_error = count_error
        self._stream_manager = stream_manager
        self._create_result = create_result
        self._create_error = create_error

    async def count_tokens(self, *, model, system, messages):
        self.calls["count_tokens"].append({"model": model, "system": system, "messages": messages})
        if self._count_error is not None:
            raise self._count_error
        return SimpleNamespace(input_tokens=self._count_value)

    def stream(self, **kwargs):
        self.calls["stream"].append(kwargs)
        return self._stream_manager

    async def create(self, **kwargs):
        self.calls["create"].append(kwargs)
        if self._create_error is not None:
            raise self._create_error
        return self._create_result


class FakeModelsNamespace:
    def __init__(self, pages=None, error=None):
        self._pages = pages or []
        self._error = error
        self.calls = []

    async def list(self, after_id=None):
        self.calls.append(after_id)
        if self._error is not None:
            raise self._error
        for page in self._pages:
            if page["after"] == after_id:
                return SimpleNamespace(data=page["items"], has_more=page["has_more"])
        return SimpleNamespace(data=[], has_more=False)


def model_item(mid, display=None):
    return SimpleNamespace(id=mid, display_name=display or mid, created_at="", type="model")


class FakeClient:
    def __init__(self, messages=None, models=None):
        self.messages = messages or FakeMessagesNamespace()
        self.models = models or FakeModelsNamespace()
