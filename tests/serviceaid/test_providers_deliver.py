"""PostmanDeliverer enqueues the grant on a Poster with the right dest/topic."""
from keri.core import serdering
from keri_serviceaid import PostmanDeliverer, Endpoint, Context


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
