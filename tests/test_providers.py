"""Тесты пула ключей serper и сбора ключей из окружения (без сети)."""

import pytest

from plagiarism.providers import (
    ProviderError,
    SerperProvider,
    collect_serper_keys,
    get_provider,
)


def test_collect_keys_dedup_and_order():
    env = {
        "SERPER_API_KEY": "main",
        "SERPER_API_KEYS": "a, b  c\nd",
        "SERPER_API_KEY_2": "main",   # дубль — не добавится
        "SERPER_API_KEY_3": "e",
    }
    keys = collect_serper_keys(env)
    assert keys[0] == "main"
    assert set(keys) == {"main", "a", "b", "c", "d", "e"}
    assert len(keys) == len(set(keys))  # без дублей


def test_get_provider_builds_pool():
    p = get_provider("serper", {"SERPER_API_KEYS": "k1,k2,k3,k4"})
    assert p.key_count == 4
    assert p.free_limit == 2500 * 4   # общий запас растёт с числом ключей


def test_empty_keys_raise():
    with pytest.raises(ProviderError):
        get_provider("serper", {})


def test_key_rotation_advances_past_exhausted():
    p = SerperProvider(["k1", "k2", "k3"])
    assert p._idx == 0
    p._exhausted.add(0)                # первый ключ «кончился»
    assert p._advance_key() is True
    assert p._idx == 1
    p._exhausted.add(1)
    assert p._advance_key() is True
    assert p._idx == 2


def test_rotation_returns_false_when_all_exhausted():
    p = SerperProvider(["k1", "k2"])
    p._exhausted.update({0, 1})
    assert p._advance_key() is False   # сигнал: все ключи исчерпаны -> RateLimitError
