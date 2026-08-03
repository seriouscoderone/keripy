# -*- encoding: utf-8 -*-
"""
keri.app.prodding module

Responder for the Prod ('pro') / Bare ('bar') content-disclosure pair.

`keri.core.eventing.Kevery` handles the protocol half: it authenticates a `pro`,
confirms the requested SAID is anchored in a KEL held here, and cues the
request. It discloses nothing, because the protocol cannot know what its
operator is willing to hand over.

This module owns the other half -- the decision. Two properties are deliberate:

**Anchoring is not consent.** Committing to data in a KEL says "this existed and
I stand behind it"; it does not say "anyone may read it." So a SAD is disclosed
only if the controller has explicitly put it in `disclosable`, and the default
`policy` denies every requester even then. Opening the door is an act, not a
default.

**Refusal is silent.** An unauthorized `pro` gets nothing back, and gets exactly
what a `pro` for a SAID we have never seen gets. An explicit refusal would be an
existence oracle: it would confirm to an unauthorized party that a given SAID is
anchored here, which is precisely the metadata a SAID commitment is supposed to
withhold. The two cases are distinguished in the local log, for the operator,
and nowhere on the wire.
"""

from hio.base import doing
from hio.help import decking

from .. import help
from ..core.eventing import bare, prod
from ..kering import Kinds, Vrsn_1_0

logger = help.ogler.getLogger()


def denyAll(source, said, route, serder):
    """Default disclosure policy: refuse everyone.

    Parameters:
        source (str): qb64 AID of the authenticated requester. Verified -- the
            signature over the prod was checked against this AID's key state,
            so this is proven identity, not a claim.
        said (str): qb64 SAID of the requested SAD
        route (str): route from the prod message
        serder (SerderKERI): the prod itself, so a policy can read anything the
            requester sent. In particular its 'q' block, which is where an
            authorizing-credential reference belongs (upstream issue #520's
            'az'): 'q' is a free-form modifier block, whereas a new top-level
            field would violate the spec's "No other top-level fields are
            allowed".
    """
    return False


def openPolicy(source, said, route, serder):
    """Disclose to any authenticated requester.

    Appropriate for data that is untargeted and meant to be widely readable --
    a public attestation, say. Must be passed explicitly; it is never a default.
    """
    return True


def allowList(*sources):
    """Policy factory: disclose only to the named AIDs.

    A static roster of AIDs is app-layer permissioning, not derived authority.
    Useful for tests and closed deployments; for anything credential-governed,
    write a policy that verifies an authorizing ACDC instead -- that is what the
    `serder` argument is for.
    """
    allowed = frozenset(sources)

    def policy(source, said, route, serder):
        return source in allowed

    return policy


class ProdResponder:
    """Answers `pro` requests with signed `bar` disclosures, under policy.

    Drains dict(kin="prod") cues left by Kevery.processPro. Cues of other kinds
    are put back in order, so this can share a cue deck with receipting doers.

    Attributes:
        hab (Hab): identifier that signs the bar
        kvy (Kevery): source of prod cues
        disclosable (dict): said -> SAD the controller consents to disclose
        policy (callable): (source, said, route) -> bool audience gate
    """

    def __init__(self, hab, kvy, *, disclosable=None, policy=None,
                 pvrsn=Vrsn_1_0, kind=Kinds.json):
        """
        Parameters:
            hab (Hab): identifier whose keys sign the bar
            kvy (Kevery): Kevery whose .cues carry prod requests
            disclosable (dict|None): said -> SAD consented for disclosure.
                Absent SAIDs are withheld even when anchored. Defaults to empty.
            policy (callable|None): (source, said, route) -> bool. Defaults to
                denyAll -- disclosure must be opened deliberately.
            pvrsn (Versionage): KERI protocol version for the bar
            kind (str): serialization kind for the bar
        """
        self.hab = hab
        self.kvy = kvy
        self.disclosable = disclosable if disclosable is not None else {}
        self.policy = policy if policy is not None else denyAll
        self.pvrsn = pvrsn
        self.kind = kind

    def service(self):
        """Drain prod cues and return signed bar messages for permitted requests.

        Returns:
            bytearray: concatenated signed bar messages, empty when nothing is
                disclosed -- the same answer a not-found prod produces.
        """
        msgs = bytearray()
        held = decking.Deck()

        while self.kvy.cues:
            cue = self.kvy.cues.pull()
            if cue["kin"] != "prod":
                held.push(cue)
                continue
            if (msg := self.respond(cue)) is not None:
                msgs.extend(msg)

        while held:  # preserve cues belonging to other consumers
            self.kvy.cues.push(held.pull())

        return msgs

    def respond(self, cue):
        """Return a signed bar for one prod cue, or None to disclose nothing.

        Parameters:
            cue (dict): dict(kin="prod", said=, dest=, route=, ...) from Kevery
        """
        said = cue["said"]
        source = cue["dest"]
        route = cue["route"]

        sad = self.disclosable.get(said)
        if sad is None:
            # Anchored, but the controller never consented to disclose it.
            logger.info("Prod: %s not marked disclosable; withholding from %s",
                        said, source)
            return None

        try:
            permitted = self.policy(source, said, route, cue["serder"])
        except Exception as ex:  # noqa: BLE001 - a policy is caller code
            # Fail CLOSED. A policy that raises is a policy that did not say
            # yes, and a credential-verifying one has plenty of ways to raise
            # (missing registry, unresolvable chain, malformed 'az'). Letting
            # this propagate would also abort the rest of the cue drain, so one
            # bad request could stall every pending disclosure.
            logger.error("Prod: policy raised for %s from %s, denying: %s",
                         said, source, ex)
            return None

        if not permitted:
            logger.info("Prod: policy denied %s to %s", said, source)
            return None

        serder = bare(pre=self.hab.pre, route=route, data={said: dict(sad)},
                      pvrsn=self.pvrsn, kind=self.kind)
        logger.info("Prod: disclosing %s to %s", said, source)
        return self.hab.endorse(serder=serder, last=False, framed=True,
                                gvrsn=self.pvrsn)


class ProdResponderDoer(doing.Doer):
    """Doer wrapper that services a ProdResponder each cycle and sends the
    resulting bar messages via a supplied callable.

    Parameters:
        responder (ProdResponder): the policy-bearing responder
        send (callable): send(msg: bytearray) -> None transport hook
    """

    def __init__(self, responder, send, **kwa):
        self.responder = responder
        self.send = send
        super(ProdResponderDoer, self).__init__(**kwa)

    def recur(self, tyme):
        """Service pending prod cues once per cycle."""
        if msgs := self.responder.service():
            self.send(msgs)
        return False


class ProdClient:
    """The requesting half of prod/bare.

    ProdResponder answers prods; this asks. Correlation is by the requested
    SAID because bar.a is keyed by it -- and eventing.bare() does not enforce
    that keying, so harvest() checks rather than assumes.
    """

    def __init__(self, hab):
        self.hab = hab

    def request(self, pre, said, route="/sealed", az=None):
        """Signed `pro` bytes (a `bytearray`, as `Hab.endorse` returns) asking
        for the body behind `said`.

        `pre` is the peer the request is FOR -- a routing selector, not a wire
        field. A `pro` carries no recipient: `prod(pre=...)` is the *sender's*
        AID, and which peer receives the request is decided by delivery (whose
        Parser gets the bytes in direct mode; which endpoint is POSTed to in a
        deployment). `pre` is therefore accepted so callers and transports can
        address the request, and by design does not affect the signed bytes.
        """
        query = dict(d=said)
        if az is not None:
            query["az"] = az          # spec: no other TOP-LEVEL fields allowed
        serder = prod(pre=self.hab.pre, route=route, query=query)
        return self.hab.endorse(serder=serder, last=False, framed=True)

    def harvest(self, serder, said):
        """Body for `said` from a received `bar`, or None if absent."""
        data = serder.ked.get("a")
        if not isinstance(data, dict):
            return None
        found = data.get(said)
        return found if isinstance(found, dict) else None
