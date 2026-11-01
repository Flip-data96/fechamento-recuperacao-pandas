import pandas as pd
import pytest

from src.fechamento import (
    calcular_honorario,
    conferir_totais,
    fechar_por_cliente,
    limpar_pagamentos,
    validar_pagamentos,
)


@pytest.fixture
def regras():
    return pd.DataFrame({
        "id_cliente": ["A", "A", "A", "B"],
        "faixa_min": [0, 50_000, 200_000, 0],
        "faixa_max": [50_000, 200_000, None, None],
        "percentual": [0.20, 0.15, 0.10, 0.15],
    })


@pytest.fixture
def carteira():
    return pd.DataFrame({
        "id_devedor": [1, 2, 3],
        "id_cliente": ["A", "B", "C"],
        "valor_divida": [1000.0, 2000.0, 500.0],
        "data_entrada": pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-01"]),
    })


def faixas(regras, cliente):
    return regras[regras["id_cliente"] == cliente].sort_values("faixa_min")


def test_honorario_progressivo_atravessa_tres_faixas(regras):
    # 50.000*20% + 150.000*15% + 14.087*10% = 33.908,70
    assert calcular_honorario(214_087, faixas(regras, "A")) == 33_908.70


def test_honorario_dentro_da_primeira_faixa(regras):
    assert calcular_honorario(30_000, faixas(regras, "A")) == 6_000.00


def test_honorario_percentual_unico_sem_limite(regras):
    assert calcular_honorario(1_000, faixas(regras, "B")) == 150.00


def test_validacao_detecta_todos_os_tipos_de_erro(carteira, regras):
    pagamentos = pd.DataFrame({
        "id_pagamento": [1, 1, 2, 3, 4, 5],
        "id_devedor": [1, 1, 1, 99, 1, 3],
        "valor_pago": [100.0, 100.0, -5.0, 50.0, 80.0, 70.0],
        "data_pagamento": pd.to_datetime(
            ["2026-02-01", "2026-02-01", "2026-02-02", "2026-02-03", "2025-12-01", "2026-02-05"]),
    })
    erros = validar_pagamentos(pagamentos, carteira, regras)
    assert set(erros["erro"]) == {
        "pagamento duplicado",
        "valor inválido",
        "devedor inexistente",
        "pagamento anterior à entrada da dívida",
        "cliente sem regra de honorário",
    }


def test_limpeza_remove_registros_invalidos(carteira, regras):
    pagamentos = pd.DataFrame({
        "id_pagamento": [1, 1, 2, 3, 4, 5, 6],
        "id_devedor": [1, 1, 1, 99, 1, 3, 2],
        "valor_pago": [100.0, 100.0, -5.0, 50.0, 80.0, 70.0, 40.0],
        "data_pagamento": pd.to_datetime(
            ["2026-02-01", "2026-02-01", "2026-02-02", "2026-02-03", "2025-12-01", "2026-02-05", "2026-02-06"]),
    })
    limpo = limpar_pagamentos(pagamentos, carteira, regras)
    assert sorted(limpo["id_pagamento"]) == [1, 6]
    assert set(limpo["mes"]) == {"2026-02"}


def test_fechamento_bate_com_a_base(carteira, regras):
    base = pd.DataFrame({
        "id_pagamento": [1, 2, 3],
        "id_devedor": [1, 1, 2],
        "id_cliente": ["A", "A", "B"],
        "valor_pago": [300.0, 200.0, 400.0],
    })
    fech = fechar_por_cliente(base, carteira, regras, "2026-02")
    a = fech[fech["id_cliente"] == "A"].iloc[0]
    assert a["recuperado"] == 500.0 and a["honorario"] == 100.0 and a["repasse_cliente"] == 400.0
    assert a["qtd_pagamentos"] == 2 and a["devedores_pagantes"] == 1
    assert conferir_totais(base, fech)["total_fechado"] == 900.0


def test_conferencia_acusa_diferenca(regras, carteira):
    base = pd.DataFrame({"id_pagamento": [1], "id_devedor": [1], "id_cliente": ["A"], "valor_pago": [100.0]})
    fech = fechar_por_cliente(base, carteira, regras, "2026-02")
    fech.loc[0, "recuperado"] = 999.0
    with pytest.raises(ValueError):
        conferir_totais(base, fech)
