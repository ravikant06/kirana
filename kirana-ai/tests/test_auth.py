"""Phase 5: who is asking. Token attacks against the real verifier, and order tools that carry no identity."""
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from conftest import FakeAdapter, make_token
from kirana_ai import agent, auth, kirana
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


# --- order tools ---------------------------------------------------------------------------

ORDER = {"id": 42, "status": "PAID", "total": 230.0, "createdAt": "2026-10-04T10:00:00Z",
         "items": [{"productName": "Paneer (200 g)", "quantity": 2, "unitPrice": 90.0, "lineTotal": 180.0}]}


SHOPPER = frozenset({"shop", "chat"})
CHAT_ONLY = frozenset({"chat"})


def _agent_turn(monkeypatch, tool_call, token, scopes=SHOPPER):
    sent = {}
    monkeypatch.setattr(kirana, "my_orders", lambda t: sent.setdefault("token", t) and [ORDER])
    monkeypatch.setattr(kirana, "my_order", lambda t, oid: (sent.update(token=t, order_id=oid), None)[1])
    llm = FakeAdapter([LLMResponse(tool_calls=(tool_call,), usage=Usage(10, 1)),
                       LLMResponse(text="ok", usage=Usage(10, 1))])
    events = list(agent.answer_stream("q", llm=llm, user_token=token, user_scopes=scopes))
    return sent, llm, [e.data for e in events if e.kind == "step"]


def test_order_tools_exist_only_for_a_token_with_shop():
    public = {"search_products", "search_docs", "list_documents"}
    assert {t.name for t in agent.tools_for(None)} == public
    assert {t.name for t in agent.tools_for("tok", CHAT_ONLY)} == public          # G1: signed in, no "shop"
    assert {"get_my_orders", "get_order"} <= {t.name for t in agent.tools_for("tok", SHOPPER)}


def test_order_tools_have_no_identity_parameter():
    for tool in agent.ORDER_TOOLS:
        params = set(tool.parameters.get("properties", {}))
        assert not params & {"user_id", "userId", "user", "customer_id", "token"}


def test_get_my_orders_forwards_the_callers_token(monkeypatch):
    sent, _, steps = _agent_turn(monkeypatch, ToolCall(id="1", name="get_my_orders", arguments={}), "tok-7")
    assert sent["token"] == "tok-7" and steps[0]["count"] == 1


def test_a_foreign_or_missing_order_is_not_found(monkeypatch):
    sent, llm, steps = _agent_turn(monkeypatch, ToolCall(id="1", name="get_order", arguments={"order_id": 99}), "tok-7")
    assert sent == {"token": "tok-7", "order_id": 99} and steps[0]["count"] == 0
    tool_result = llm.calls[1][-1].tool_result.content
    assert tool_result["found"] is False


def test_a_hallucinated_order_call_without_a_token_gets_nothing(monkeypatch):
    sent, _, steps = _agent_turn(monkeypatch, ToolCall(id="1", name="get_my_orders", arguments={}), None)
    assert sent == {} and steps[0]["count"] == 0


def test_permissions_come_from_the_tokens_scope():
    shopper = auth.verify(make_token(7))
    assert shopper.role == "SHOPPER" and shopper.scopes == {"shop", "chat"}
    with pytest.raises(auth.Forbidden, match="kb:write"):
        shopper.require("kb:write")
    assert auth.verify(make_token(1, role="ADMIN", scope="chat kb:write")).require("kb:write")


# --- G1: a chat-only account (found by experiment: the turn used to fail with 503) -----------

def test_g1_an_order_call_without_shop_is_refused_before_kirana(monkeypatch):
    sent, llm, steps = _agent_turn(monkeypatch, ToolCall(id="1", name="get_my_orders", arguments={}),
                                   "tok-2", scopes=CHAT_ONLY)
    assert sent == {}                                          # Kirana never asked
    result = llm.calls[1][-1].tool_result.content
    assert result["denied_by"] == "policy" and "not allowed" in result["error"]


def test_g1_kiranas_403_is_a_tool_result_not_an_outage(monkeypatch):
    def refuse(token):
        raise kirana.NotPermitted()
    monkeypatch.setattr(kirana, "my_orders", refuse)
    llm = FakeAdapter([LLMResponse(tool_calls=(ToolCall(id="1", name="get_my_orders", arguments={}),), usage=Usage(10, 1)),
                       LLMResponse(text="Your account can't view orders.", usage=Usage(10, 1))])
    events = list(agent.answer_stream("my orders?", llm=llm, user_token="tok", user_scopes=SHOPPER))
    assert events[-1].kind == "done" and "can't view orders" in events[-1].data["answer"]
    assert llm.calls[1][-1].tool_result.content["denied_by"] == "kirana"


def test_kirana_client_maps_403_to_not_permitted(monkeypatch):
    import httpx
    transport = httpx.MockTransport(lambda req: httpx.Response(403, json={"code": "FORBIDDEN"}))
    monkeypatch.setattr(kirana, "_client", lambda: httpx.Client(base_url="http://kirana", transport=transport))
    with pytest.raises(kirana.NotPermitted):
        kirana.my_orders("tok")
