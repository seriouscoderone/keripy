"""PostmanDeliverer enqueues the grant on a Poster with the right dest/topic."""
from hio.base import doing
from keri.core import serdering
from keri_serviceaid import PostmanDeliverer, Endpoint, Context
from keri_serviceaid.providers.deliver import _drive_until_sent


class _FakePoster(doing.DoDoer):
    """A Poster stand-in that reports sent() only after `sent_after` recur passes."""
    def __init__(self, sent_after):
        self._n = 0
        self._sent_after = sent_after
        super().__init__(doers=[doing.doify(self._run)])

    def _run(self, tymth=None, tock=0.0, **kwa):
        while True:
            self._n += 1
            yield

    def sent(self, said):
        return self._n >= self._sent_after


def test_drive_until_sent_exits_early_when_sent():
    # A real Doist driving the fake poster; no real waiting (sleep is a no-op).
    poster = _FakePoster(sent_after=3)
    doist = doing.Doist(real=True, tock=0.03125)
    deeds = doist.enter(doers=[poster])
    slept = []
    try:
        _drive_until_sent(doist, deeds, poster, "Esaid", timeout=100.0,
                          sleep=slept.append, now=lambda: 0.0)
    finally:
        doist.exit(deeds=deeds)
    assert poster.sent("Esaid")          # exited because it became sent
    assert len(slept) < 100              # early-exit, not run to the timeout


def test_drive_until_sent_honors_deadline_when_never_sent():
    # Poster never reports sent; a monotonic clock that marches past the timeout must
    # break the loop (no infinite hang) even though sent() stays False.
    poster = _FakePoster(sent_after=10**9)
    doist = doing.Doist(real=True, tock=0.03125)
    deeds = doist.enter(doers=[poster])
    ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    try:
        _drive_until_sent(doist, deeds, poster, "Esaid", timeout=5.0,
                          sleep=lambda _t: None, now=lambda: next(ticks))
    finally:
        doist.exit(deeds=deeds)
    assert not poster.sent("Esaid")      # gave up at the deadline, did not hang


class FakePoster:
    def __init__(self):
        self.calls = []

    def send(self, dest, topic, serder, src=None, hab=None, attachment=None):
        self.calls.append(dict(dest=dest, topic=topic, serder=serder,
                               src=src, hab=hab, attachment=attachment))


def test_deliver_calls_poster_send_with_dest_and_topic():
    poster = FakePoster()
    deliverer = PostmanDeliverer(poster=poster)

    from keri.app.habbing import Habery
    from keri.core.signing import Salter
    from _exn import make_exn
    hby = Habery(name="svc", temp=True, salt=Salter(raw=b'0123456789abcdef').qb64)
    hab = hby.makeHab(name="svc", transferable=True)
    exn = make_exn("/ipex/grant", hab.pre, attributes={})
    msg = bytearray(exn.raw)

    ep = Endpoint(role="mailbox", eid="EMbx", url="https://mailbox.keri.host",
                  cid="EReq")
    ctx = Context(hby=hby, hab=hab, rgy=None, registry_name="svc")
    deliverer.deliver(bytes(msg), ep, ctx)

    assert len(poster.calls) == 1
    call = poster.calls[0]
    # deliver to the RECIPIENT (cid), not the mailbox provider (eid). Poster.forward()
    # then resolves the recipient's mailbox and /fwd-posts there; sending to the provider
    # would take the provider's own controller endpoint and bare-POST (dropped by a
    # serverless mailbox, which only stores /fwd-addressed mail).
    assert call["dest"] == "EReq"
    assert call["topic"] == "credential"
    assert call["hab"] is hab
    assert isinstance(call["serder"], serdering.SerderKERI)
    hby.close()
