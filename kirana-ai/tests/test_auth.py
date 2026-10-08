"""Phase 5: who is asking. Token attacks against the real verifier, and order tools that carry no identity."""
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from conftest import FakeAdapter, make_token
from kirana_ai import agent, auth, kirana, policy
from kirana_ai.llm import LLMResponse, ToolCall, Usage


def test_a_valid_token_names_its_user():
    caller = auth.from_header("Bearer " + make_token(7))
    assert caller.user_id == 7


@pytest.mark.parametrize("header, reason", [
    (None, "sign in first"),
    ("X-User-Id 7", "sign in first"),
    ("Bearer not-a-jwt", "malformed"),
])
def test_no_or_garbage_token_is_refused(header, reason):
    with pytest.raises(auth.InvalidToken, match=reason):
        auth.from_header(header)


def test_an_edited_subject_breaks_the_signature():
    header, payload, sig = make_token(1).split(".")
    forged_payload = jwt.utils.base64url_encode(
        jwt.utils.base64url_decode(payload).replace(b'"sub":"1"', b'"sub":"2"')).decode()
    with pytest.raises(auth.InvalidToken, match="Signature verification failed"):
        auth.verify(f"{header}.{forged_payload}.{sig}")


def test_a_token_signed_with_another_key_is_refused():
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(auth.InvalidToken, match="Signature verification failed"):
        auth.verify(make_token(1, key=attacker))       # same kid, wrong private key


def test_alg_none_is_refused():
    unsigned = jwt.encode({"sub": "1", "aud": "kirana-ai", "iss": "kirana", "exp": 9999999999},
                          None, algorithm="none", headers={"kid": "test-key"})
    with pytest.raises(auth.InvalidToken):
        auth.verify(unsigned)


@pytest.mark.parametrize("kw, reason", [
    ({"aud": "kirana-api"}, "Audience"),          # a token for Kirana only
    ({"iss": "evil"}, "issuer"),
    ({"expires_in": -120}, "expired"),           # beyond the 30 s leeway
    ({"kid": "unknown"}, "unknown signing key"),
])
def test_wrong_audience_issuer_expiry_or_key_is_refused(kw, reason):
    with pytest.raises(auth.InvalidToken, match=f"(?i){reason}"):
        auth.verify(make_token(1, **kw))


def test_jwks_unreachable_fails_closed(monkeypatch):
    class Down:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientConnectionError("connection refused")
    monkeypatch.setattr(auth, "_jwks", lambda: Down())
    from kirana_ai.errors import UpstreamUnavailable
    with pytest.raises(UpstreamUnavailable):
        auth.verify(make_token(1))


# --- order tools (identity through narrowed credentials, never the model) ------------------

ORDER = {"id": 42, "status": "PAID", "total": 230.0, "createdAt": "2026-10-04T10:00:00Z",
         "items": [{"productName": "Paneer (200 g)", "quantity": 2, "unitPrice": 90.0, "lineTotal": 180.0}]}
SHOPPER = frozenset({"orders:read", "orders:write", "cart:read", "cart:write", "chat"})
CHAT_ONLY = frozenset({"chat"})


class FakeCredentials:
    """Records which scopes the tools asked for; hands out a token naming them."""
    def __init__(self):
        self.asked = []

    def token(self, *scopes):
        self.asked.append(scopes)
        return "narrow:" + " ".join(sorted(scopes))


def caller_with(scopes=SHOPPER, user_id=7):
    return policy.Caller(user_id=user_id, scopes=scopes, credentials=FakeCredentials())


def _agent_turn(monkeypatch, tool_call, caller):
    sent, recorded = {}, []
    monkeypatch.setattr(kirana, "my_orders", lambda t: sent.setdefault("token", t) and [ORDER])
    monkeypatch.setattr(kirana, "my_order", lambda t, oid: (sent.update(token=t, order_id=oid), None)[1])
    llm = FakeAdapter([LLMResponse(tool_calls=(tool_call,), usage=Usage(10, 1)),
                       LLMResponse(text="ok", usage=Usage(10, 1))])
    events = list(agent.answer_stream("q", llm=llm, caller=caller, decisions=lambda r: recorded.append(r) or True))
    return sent, llm, [e.data for e in events if e.kind == "step"], recorded


def test_order_tools_exist_only_for_a_caller_with_orders_read():
    public = {"search_products", "search_docs", "list_documents"}
    assert {t.name for t in policy.tools_for(policy.ANONYMOUS, agent.SPECS)} == public
    # G1: a chat-only account gets no order tools (it may still save its own preferences)
    assert {t.name for t in policy.tools_for(caller_with(CHAT_ONLY), agent.SPECS)} == public | {"remember_preference"}
    assert {"get_my_orders", "get_order", "cancel_order", "add_to_cart"} <= \
        {t.name for t in policy.tools_for(caller_with(), agent.SPECS)}


def test_no_tool_has_an_identity_parameter():
    for tool in agent.SPECS.values():
        params = set(tool.parameters.get("properties", {}))
        assert not params & {"user_id", "userId", "user", "customer_id", "token", "email"}


def test_get_my_orders_uses_a_narrowed_read_token(monkeypatch):
    caller = caller_with()
    sent, _, steps, _ = _agent_turn(monkeypatch, ToolCall(id="1", name="get_my_orders", arguments={}), caller)
    assert sent["token"] == "narrow:orders:read"         # never the shopper's own token
    assert caller.credentials.asked == [("orders:read",)] and steps[0]["count"] == 1


def test_a_foreign_or_missing_order_is_not_found_and_logged_as_kirana(monkeypatch):
    sent, llm, steps, recorded = _agent_turn(
        monkeypatch, ToolCall(id="1", name="get_order", arguments={"order_id": 99}), caller_with())
    assert sent["order_id"] == 99 and steps[0]["count"] == 0
    assert llm.calls[1][-1].tool_result.content["found"] is False
    assert [(r.decision.outcome.value, r.decision.layer) for r in recorded] == [("allow", "policy"), ("deny", "kirana")]


def test_an_anonymous_order_call_gets_nothing(monkeypatch):
    sent, _, steps, recorded = _agent_turn(
        monkeypatch, ToolCall(id="1", name="get_my_orders", arguments={}), policy.ANONYMOUS)
    assert sent == {} and steps[0]["decision"] == "deny" and steps[0]["denied_by"] == "policy"


# --- G1: a chat-only account (found by experiment: the turn used to fail with 503) -----------

def test_g1_an_order_call_without_orders_read_is_refused_before_kirana(monkeypatch):
    sent, llm, steps, recorded = _agent_turn(
        monkeypatch, ToolCall(id="1", name="get_my_orders", arguments={}), caller_with(CHAT_ONLY))
    assert sent == {}                                          # Kirana never asked
    result = llm.calls[1][-1].tool_result.content
    assert result["denied_by"] == "policy" and "not allowed" in result["error"]
    assert recorded[0].decision.reason == "missing-scope:orders:read"


def test_g1_kiranas_403_is_a_tool_result_not_an_outage(monkeypatch):
    def refuse(token):
        raise kirana.NotPermitted()
    monkeypatch.setattr(kirana, "my_orders", refuse)
    llm = FakeAdapter([LLMResponse(tool_calls=(ToolCall(id="1", name="get_my_orders", arguments={}),), usage=Usage(10, 1)),
                       LLMResponse(text="Your account can't view orders.", usage=Usage(10, 1))])
    events = list(agent.answer_stream("my orders?", llm=llm, caller=caller_with()))
    assert events[-1].kind == "done" and "can't view orders" in events[-1].data["answer"]
    assert llm.calls[1][-1].tool_result.content["denied_by"] == "kirana"


def test_kirana_client_maps_403_to_not_permitted(monkeypatch):
    import httpx
    transport = httpx.MockTransport(lambda req: httpx.Response(403, json={"code": "FORBIDDEN"}))
    monkeypatch.setattr(kirana, "_client", lambda: httpx.Client(base_url="http://kirana", transport=transport))
    with pytest.raises(kirana.NotPermitted):
        kirana.my_orders("tok")
