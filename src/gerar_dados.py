"""Gera dados FICTÍCIOS de carteira e pagamentos para o projeto.

Inclui erros plantados de propósito (duplicidade, valor inválido, devedor
inexistente, pagamento antes da entrada da dívida e cliente sem regra de
honorário) para exercitar a etapa de validação.

Uso:
    python -m src.gerar_dados --seed 42
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

CLIENTES = ["BancoA", "VarejoB", "TelecomC"]


def gerar(pasta: str = "data", seed: int = 42, n_devedores: int = 300, n_pagamentos: int = 900) -> None:
    rng = np.random.default_rng(seed)

    carteira = pd.DataFrame({
        "id_devedor": np.arange(1, n_devedores + 1),
        "id_cliente": rng.choice(CLIENTES, n_devedores, p=[0.40, 0.35, 0.25]),
        "valor_divida": np.round(rng.uniform(800, 25000, n_devedores), 2),
        "data_entrada": pd.Timestamp("2026-01-01") + pd.to_timedelta(rng.integers(0, 195, n_devedores), unit="D"),
    })
    # Cliente sem regra de honorário cadastrada (erro plantado)
    extra = pd.DataFrame({
        "id_devedor": [n_devedores + 1, n_devedores + 2],
        "id_cliente": ["SaudeD", "SaudeD"],
        "valor_divida": [4300.00, 9100.00],
        "data_entrada": pd.to_datetime(["2026-03-10", "2026-05-02"]),
    })
    carteira = pd.concat([carteira, extra], ignore_index=True)

    pagamentos = pd.DataFrame({
        "id_pagamento": np.arange(1, n_pagamentos + 1),
        "id_devedor": rng.choice(carteira["id_devedor"], n_pagamentos),
        "valor_pago": np.round(rng.uniform(50, 3000, n_pagamentos), 2),
        "data_pagamento": pd.Timestamp("2026-08-01") + pd.to_timedelta(rng.integers(0, 92, n_pagamentos), unit="D"),
    })

    # ---- erros plantados ----
    pagamentos = pd.concat([pagamentos, pagamentos.iloc[[10]]], ignore_index=True)   # duplicado
    pagamentos.loc[20, "valor_pago"] = -150.0                                        # valor inválido
    pagamentos.loc[30, "id_devedor"] = 99999                                         # devedor inexistente
    devedor = pagamentos.loc[40, "id_devedor"]
    entrada = carteira.loc[carteira["id_devedor"] == devedor, "data_entrada"].iloc[0]
    pagamentos.loc[40, "data_pagamento"] = entrada - pd.Timedelta(days=10)           # antes da entrada

    destino = Path(pasta)
    destino.mkdir(parents=True, exist_ok=True)
    carteira.to_csv(destino / "carteira.csv", index=False)
    pagamentos.to_csv(destino / "pagamentos.csv", index=False)
    print(f"Dados gerados em '{destino}': {len(carteira)} devedores e {len(pagamentos)} pagamentos (seed={seed}).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Gera dados fictícios para o fechamento.")
    ap.add_argument("--pasta", default="data")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    gerar(args.pasta, args.seed)
