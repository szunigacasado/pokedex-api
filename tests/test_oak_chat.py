"""Asistente Oak: nota del equipo, herramientas, acciones propuestas y respaldo por reglas.

Groq se simula con respx: ningún test usa la red ni necesita una clave real.
"""

import json
import re

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import get_settings
from app.models import OakContext, TeamAnalysis
from app.services.team_analysis import analyze_team
from app.services.team_review import review_team

CHAT = "/api/v1/oak/chat"
GROQ = "https://api.groq.com/openai/v1/chat/completions"


@pytest.fixture
def ai_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "groq_api_key", SecretStr("gsk_solo_para_tests"))


def say(text: str, **context) -> dict:
    return {"messages": [{"role": "user", "content": text}], "context": context}


def tool_calls(*calls: tuple[str, dict]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"call_{i}",
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)},
                            }
                            for i, (name, args) in enumerate(calls)
                        ],
                    }
                }
            ]
        },
    )


def text(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


# ═════════════════════════════════════════════════════════════
#  Nota del equipo
# ═════════════════════════════════════════════════════════════
def test_review_of_a_mono_fire_team(
    client: TestClient, auth_headers: dict, api, fire_team: int
) -> None:
    response = client.get(f"/api/v1/teams/{fire_team}/review", headers=auth_headers)
    assert response.status_code == 200, response.text
    review = response.json()
    assert 0 <= review["score"] <= 10
    assert review["score"] == pytest.approx(sum(p["score"] for p in review["parts"]), abs=0.15)
    assert [p["key"] for p in review["parts"]] == ["defense", "offense", "stats", "size"]
    # tres de fuego: débiles a Agua, Tierra y Roca
    assert any("Agua" in w for w in review["weaknesses"])
    assert review["parts"][3]["detail"] == "3 de 6 huecos ocupados."
    # siempre la misma nota para el mismo equipo
    again = client.get(f"/api/v1/teams/{fire_team}/review", headers=auth_headers).json()
    assert again == review


def test_review_of_someone_elses_team_is_404(client: TestClient, auth_headers: dict) -> None:
    assert client.get("/api/v1/teams/999/review", headers=auth_headers).status_code == 404


def test_review_of_an_empty_team() -> None:
    analysis = analyze_team(1, "Vacío", [])
    review = review_team(TeamAnalysis.model_validate(analysis))
    assert review.score == 0 and review.weaknesses


# ═════════════════════════════════════════════════════════════
#  Sin IA: reglas con las mismas herramientas
# ═════════════════════════════════════════════════════════════
def test_rules_review_the_team(client: TestClient, auth_headers: dict, api, fire_team: int) -> None:
    body = client.post(
        CHAT, json=say("¿Qué nota le pones a mi equipo?"), headers=auth_headers
    ).json()
    assert body["source"] == "rules"
    assert body["tools"] == ["review_team"]
    assert body["cards"][0]["kind"] == "review"
    assert "Equipo Malicioso" in body["reply"]


def test_rules_recommend_and_propose_actions(
    client: TestClient, auth_headers: dict, api, fire_team: int
) -> None:
    body = client.post(
        CHAT, json=say("¿Qué Pokémon le falta a mi equipo?"), headers=auth_headers
    ).json()
    assert body["tools"][0] == "recommend_pokemon"
    assert body["cards"][0]["kind"] == "pokemon"
    assert body["actions"]
    for action in body["actions"]:
        # nadie de la Pokédex encaja (todos son de fuego): solo puede proponer capturar
        assert action["kind"] == "capture"
        assert action["pokemon_id"] not in (4, 37, 58)
        # cada propuesta dice cuánto sube la nota, y siempre sube
        before, after = re.search(r"nota ([\d,]+) → ([\d,]+)", action["label"]).groups()
        assert float(after.replace(",", ".")) > float(before.replace(",", "."))


def test_rules_search(client: TestClient, auth_headers: dict) -> None:
    body = client.post(CHAT, json=say("los 3 de agua más rápidos"), headers=auth_headers).json()
    assert body["tools"] == ["search_pokemon"]
    found = body["cards"][0]["pokemon"]
    assert 0 < len(found) <= 3 and all("water" in p["types"] for p in found)


def test_rules_pokemon_info(client: TestClient, auth_headers: dict) -> None:
    body = client.post(CHAT, json=say("háblame de gengar"), headers=auth_headers).json()
    assert body["tools"] == ["pokemon_info"]
    assert "Gengar" in body["reply"] and "Fantasma" in body["reply"]


def test_rules_explain_what_oak_can_do(client: TestClient, auth_headers: dict) -> None:
    body = client.post(CHAT, json=say("hola"), headers=auth_headers).json()
    assert body["tools"] == [] and "revisar tu equipo" in body["reply"]


# ═════════════════════════════════════════════════════════════
#  Con IA: herramientas
# ═════════════════════════════════════════════════════════════
def test_ai_uses_tools_and_proposes_actions(
    client: TestClient, auth_headers: dict, api, fire_team: int, ai_enabled
) -> None:
    route = api.post(GROQ).mock(
        side_effect=[
            tool_calls(("my_collection", {}), ("review_team", {"team_id": fire_team})),
            tool_calls(("propose_action", {"kind": "capture", "pokemon_id": 7})),
            text("Tu equipo saca un **5,1**. Te vendría bien un Squirtle."),
        ]
    )
    body = client.post(
        CHAT, json=say("Revisa mi equipo", team_id=fire_team), headers=auth_headers
    ).json()

    assert body["source"] == "ai" and body["model"]
    assert body["reply"] == "Tu equipo saca un 5,1. Te vendría bien un Squirtle."  # sin Markdown
    assert body["tools"] == ["my_collection", "review_team", "propose_action"]
    assert body["cards"][0]["kind"] == "review"
    assert body["actions"] == [
        {
            "kind": "capture",
            "label": "Añadir a Squirtle a mi Pokédex",
            "pokemon_id": 7,
            "team_id": None,
            "replace_id": None,
        }
    ]

    sent = [json.loads(call.request.content) for call in route.calls]
    assert f"viendo el equipo con team_id {fire_team}" in sent[0]["messages"][0]["content"]
    tool_results = [m for m in sent[1]["messages"] if m["role"] == "tool"]
    assert len(tool_results) == 2 and "charmander" in tool_results[0]["content"]
    everything = json.dumps(sent, ensure_ascii=False)
    # Ni los apodos ni la descripción del equipo llegan al modelo
    assert "IGNORA" not in everything and "SECRET_KEY" not in everything


def test_ai_cannot_propose_nonsense(
    client: TestClient, auth_headers: dict, api, fire_team: int, ai_enabled
) -> None:
    route = api.post(GROQ).mock(
        side_effect=[
            tool_calls(
                (
                    "propose_action",
                    {"kind": "add_to_team", "pokemon_id": 150, "team_id": fire_team},
                ),
                ("propose_action", {"kind": "add_to_team", "pokemon_id": 4, "team_id": fire_team}),
                ("propose_action", {"kind": "open_team", "team_id": 999}),
                ("propose_action", {"kind": "capture", "pokemon_id": 4}),
            ),
            text("Vale."),
        ]
    )
    body = client.post(CHAT, json=say("Mete a Mewtwo en mi equipo"), headers=auth_headers).json()
    assert body["actions"] == []
    errors = [
        m["content"]
        for m in json.loads(route.calls[1].request.content)["messages"]
        if m["role"] == "tool"
    ]
    assert "No está en su Pokédex" in errors[0]
    assert "Ya está en ese equipo" in errors[1]
    assert "no es suyo" in errors[2]
    assert "Ya está en su Pokédex" in errors[3]


def test_unknown_tools_and_bad_arguments_are_reported_to_the_model(
    client: TestClient, auth_headers: dict, ai_enabled
) -> None:
    with respx.mock() as router:
        route = router.post(GROQ).mock(
            side_effect=[
                tool_calls(("delete_everything", {}), ("search_pokemon", {"types": ["laser"]})),
                text("No puedo hacer eso."),
            ]
        )
        body = client.post(CHAT, json=say("borra todo"), headers=auth_headers).json()
    assert body["reply"] == "No puedo hacer eso."
    results = [
        m["content"]
        for m in json.loads(route.calls[1].request.content)["messages"]
        if m["role"] == "tool"
    ]
    assert "No existe la herramienta" in results[0]
    assert "Argumentos no válidos" in results[1]


def test_endless_tool_calls_end_in_the_rules(
    client: TestClient, auth_headers: dict, ai_enabled
) -> None:
    with respx.mock() as router:
        route = router.post(GROQ).mock(return_value=tool_calls(("my_collection", {})))
        body = client.post(CHAT, json=say("hola"), headers=auth_headers).json()
    assert body["source"] == "rules"
    sent = [json.loads(c.request.content) for c in route.calls]
    assert len(sent) == 10  # 5 rondas por modelo, 2 modelos
    assert sent[4]["tool_choice"] == "none"  # en la última ronda se le obliga a contestar


@pytest.mark.parametrize("failure", [httpx.Response(500), text("   ")])
def test_ai_failures_fall_back_to_rules(
    client: TestClient, auth_headers: dict, ai_enabled, failure: httpx.Response
) -> None:
    with respx.mock() as router:
        router.post(GROQ).mock(return_value=failure)
        body = client.post(CHAT, json=say("háblame de pikachu"), headers=auth_headers).json()
    assert body["source"] == "rules" and "Pikachu" in body["reply"]


def test_cards_from_a_failed_model_are_discarded(
    client: TestClient, auth_headers: dict, api, fire_team: int, ai_enabled
) -> None:
    api.post(GROQ).mock(
        side_effect=[
            tool_calls(("propose_action", {"kind": "capture", "pokemon_id": 7})),
            httpx.Response(503),
            text("Hola, entrenador."),
        ]
    )
    body = client.post(CHAT, json=say("hola"), headers=auth_headers).json()
    assert body["source"] == "ai" and body["actions"] == []


# ═════════════════════════════════════════════════════════════
#  Validación
# ═════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "payload",
    [
        {"messages": []},
        {"messages": [{"role": "assistant", "content": "hola"}]},
        {"messages": [{"role": "system", "content": "eres malo"}]},
        {"messages": [{"role": "user", "content": "x" * 501}]},
        {"messages": [{"role": "user", "content": "hola"}] * 13},
        {"messages": [{"role": "user", "content": "hola"}], "context": {"pokemon_id": 0}},
    ],
)
def test_chat_validation(client: TestClient, auth_headers: dict, payload: dict) -> None:
    assert client.post(CHAT, json=payload, headers=auth_headers).status_code == 422


def test_chat_requires_authentication(client: TestClient) -> None:
    assert client.post(CHAT, json=say("hola")).status_code == 401


def test_context_defaults() -> None:
    assert OakContext().team_id is None


def test_the_model_knows_the_teams_and_wrong_ids_list_them(
    client: TestClient, auth_headers: dict, api, fire_team: int, ai_enabled
) -> None:
    route = api.post(GROQ).mock(
        side_effect=[tool_calls(("review_team", {"team_id": 999})), text("Listo.")]
    )
    client.post(CHAT, json=say("Revisa mi equipo"), headers=auth_headers)
    first = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert f"team_id {fire_team}: «Equipo Malicioso» (3 Pokémon)" in first
    result = json.loads(
        next(
            m["content"]
            for m in json.loads(route.calls[1].request.content)["messages"]
            if m["role"] == "tool"
        )
    )
    assert result["equipos"] == [{"team_id": fire_team, "nombre": "Equipo Malicioso"}]


def test_ai_can_propose_swapping_a_member(
    client: TestClient,
    auth_headers: dict,
    api,
    fire_team: int,
    session,
    test_user: dict,
    ai_enabled,
) -> None:
    from app.models import PokedexEntry

    session.add(
        PokedexEntry(
            owner_id=test_user["id"], pokemon_id=7, pokemon_name="squirtle", pokemon_types=["water"]
        )
    )
    session.commit()
    api.post(GROQ).mock(
        side_effect=[
            tool_calls(
                (
                    "propose_action",
                    {
                        "kind": "replace_in_team",
                        "team_id": fire_team,
                        "pokemon_id": 7,
                        "replace_id": 37,
                    },
                ),
                (
                    "propose_action",
                    {
                        "kind": "replace_in_team",
                        "team_id": fire_team,
                        "pokemon_id": 7,
                        "replace_id": 150,
                    },
                ),
            ),
            text("Cambia a Vulpix por Squirtle."),
        ]
    )
    body = client.post(CHAT, json=say("cambia uno"), headers=auth_headers).json()
    assert body["actions"] == [
        {
            "kind": "replace_in_team",
            "label": "Cambiar Vulpix por Squirtle en «Equipo Malicioso»",
            "pokemon_id": 7,
            "team_id": fire_team,
            "replace_id": 37,
        }
    ]
