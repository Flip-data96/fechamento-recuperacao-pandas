"""Fechamento mensal de valores recuperados e honorários.

Fluxo: carregar -> validar -> limpar -> fechar por cliente -> calcular
honorários -> conferir totais -> exportar planilhas (uma por cliente +
conferência).

Uso:
    python -m src.fechamento --mes 2026-10
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

log = logging.getLogger("fechamento")

COLS_CARTEIRA = {"id_devedor", "id_cliente", "valor_divida", "data_entrada"}
COLS_PAGAMENTOS = {"id_pagamento", "id_devedor", "valor_pago", "data_pagamento"}
COLS_REGRAS = {"id_cliente", "faixa_min", "faixa_max", "percentual"}


# ---------------------------------------------------------------- entrada
def _exigir_colunas(df: pd.DataFrame, esperadas: set[str], nome: str) -> None:
    faltando = esperadas - set(df.columns)
    if faltando:
        raise ValueError(f"{nome}: colunas ausentes {sorted(faltando)}")


def carregar_dados(pasta_dados: str, arquivo_regras: str):
    pasta = Path(pasta_dados)
    carteira = pd.read_csv(pasta / "carteira.csv", parse_dates=["data_entrada"])
    pagamentos = pd.read_csv(pasta / "pagamentos.csv", parse_dates=["data_pagamento"])
    regras = pd.read_csv(arquivo_regras)

    _exigir_colunas(carteira, COLS_CARTEIRA, "carteira.csv")
    _exigir_colunas(pagamentos, COLS_PAGAMENTOS, "pagamentos.csv")
    _exigir_colunas(regras, COLS_REGRAS, "regras_honorarios.csv")
    if carteira["id_devedor"].duplicated().any():
        raise ValueError("carteira.csv: id_devedor duplicado")
    return carteira, pagamentos, regras


# ------------------------------------------------------------- validação
def validar_pagamentos(pagamentos: pd.DataFrame, carteira: pd.DataFrame, regras: pd.DataFrame) -> pd.DataFrame:
    """Devolve as linhas com problema, com a coluna 'erro' dizendo o motivo."""
    base = pagamentos.merge(
        carteira[["id_devedor", "id_cliente", "data_entrada"]], on="id_devedor", how="left"
    )
    checks = {
        "pagamento duplicado": base.duplicated("id_pagamento", keep=False),
        "valor inválido": base["valor_pago"].isna() | (base["valor_pago"] <= 0),
        "devedor inexistente": base["id_cliente"].isna(),
        "pagamento anterior à entrada da dívida": base["data_pagamento"] < base["data_entrada"],
        "cliente sem regra de honorário": base["id_cliente"].notna()
        & ~base["id_cliente"].isin(regras["id_cliente"]),
    }
    partes = [base[mask].assign(erro=erro) for erro, mask in checks.items() if mask.any()]
    if not partes:
        return pd.DataFrame(columns=list(base.columns) + ["erro"])
    return pd.concat(partes, ignore_index=True)


def limpar_pagamentos(pagamentos: pd.DataFrame, carteira: pd.DataFrame, regras: pd.DataFrame) -> pd.DataFrame:
    """Mantém só pagamentos válidos e acrescenta cliente e mês (AAAA-MM)."""
    base = (
        pagamentos.drop_duplicates("id_pagamento", keep="first")
        .merge(carteira[["id_devedor", "id_cliente", "data_entrada"]], on="id_devedor", how="inner")
    )
    base = base[
        (base["valor_pago"] > 0)
        & (base["data_pagamento"] >= base["data_entrada"])
        & base["id_cliente"].isin(regras["id_cliente"])
    ]
    return base.assign(mes=base["data_pagamento"].dt.strftime("%Y-%m")).reset_index(drop=True)


# ---------------------------------------------------------------- cálculo
def calcular_honorario(valor: float, faixas: pd.DataFrame) -> float:
    """Honorário PROGRESSIVO: cada percentual incide só sobre a parte do valor
    que cai dentro da sua faixa. faixa_max vazia = sem limite superior."""
    total = 0.0
    for f in faixas.itertuples():
        teto = np.inf if pd.isna(f.faixa_max) else f.faixa_max
        parte = min(max(valor - f.faixa_min, 0.0), teto - f.faixa_min)
        total += parte * f.percentual
    return round(total, 2)


def fechar_por_cliente(base_mes: pd.DataFrame, carteira: pd.DataFrame, regras: pd.DataFrame, mes: str) -> pd.DataFrame:
    fech = base_mes.groupby("id_cliente", as_index=False).agg(
        qtd_pagamentos=("id_pagamento", "count"),
        devedores_pagantes=("id_devedor", "nunique"),
        recuperado=("valor_pago", "sum"),
    )
    carteira_cli = carteira.groupby("id_cliente", as_index=False).agg(carteira=("valor_divida", "sum"))
    fech = fech.merge(carteira_cli, on="id_cliente", how="left")

    faixas = {c: g.sort_values("faixa_min") for c, g in regras.groupby("id_cliente")}
    fech["recuperado"] = fech["recuperado"].round(2)
    fech["honorario"] = [calcular_honorario(v, faixas[c]) for c, v in zip(fech["id_cliente"], fech["recuperado"])]
    fech["repasse_cliente"] = (fech["recuperado"] - fech["honorario"]).round(2)
    fech["perc_efetivo"] = (fech["honorario"] / fech["recuperado"]).round(4)
    fech["taxa_recuperacao"] = (fech["recuperado"] / fech["carteira"]).round(4)
    fech.insert(1, "mes", mes)
    return fech


def conferir_totais(base_mes: pd.DataFrame, fech: pd.DataFrame) -> dict:
    """Totais de controle: o fechamento precisa bater com a base do mês."""
    bruto = round(base_mes["valor_pago"].sum(), 2)
    fechado = round(fech["recuperado"].sum(), 2)
    if abs(bruto - fechado) > 0.01:
        raise ValueError(f"Total da base ({bruto}) difere do fechamento ({fechado})")
    soma = round((fech["honorario"] + fech["repasse_cliente"]).sum(), 2)
    if abs(soma - fechado) > 0.01:
        raise ValueError(f"Honorário + repasse ({soma}) difere do recuperado ({fechado})")
    return {"total_base_mes": bruto, "total_fechado": fechado}


# ------------------------------------------------------------------ saída
def _escrever_aba(writer, df, aba, colunas_moeda=(), colunas_pct=(), coluna_total=None) -> None:
    df.to_excel(writer, sheet_name=aba, index=False)
    ws = writer.sheets[aba]
    fundo = PatternFill("solid", fgColor="1F3864")
    for idx, nome in enumerate(df.columns, start=1):
        cab = ws.cell(row=1, column=idx)
        cab.font = Font(bold=True, color="FFFFFF")
        cab.fill = fundo
        cab.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        tamanhos = [len(str(nome))] + [len(str(v)) for v in df[nome].head(200)]
        ws.column_dimensions[get_column_letter(idx)].width = min(max(tamanhos) + 3, 42)
        fmt = "#,##0.00" if nome in colunas_moeda else "0.0%" if nome in colunas_pct else None
        if fmt:
            for r in range(2, len(df) + 2):
                ws.cell(row=r, column=idx).number_format = fmt
    ws.freeze_panes = "A2"
    if coluna_total and len(df):
        idx = list(df.columns).index(coluna_total) + 1
        letra = get_column_letter(idx)
        linha = len(df) + 3
        ws.cell(row=linha, column=1, value="TOTAL").font = Font(bold=True)
        cel = ws.cell(row=linha, column=idx, value=f"=SUM({letra}2:{letra}{len(df) + 1})")
        cel.font = Font(bold=True)
        cel.number_format = "#,##0.00"


def exportar_planilhas(fech, base_mes, erros, controle, mes, pasta_saida) -> list[Path]:
    saida = Path(pasta_saida)
    saida.mkdir(parents=True, exist_ok=True)
    arquivos = []
    moeda_resumo = ("recuperado", "honorario", "repasse_cliente", "carteira")
    pct_resumo = ("perc_efetivo", "taxa_recuperacao")
    opcoes = dict(engine="openpyxl", datetime_format="dd/mm/yyyy", date_format="dd/mm/yyyy")

    for cliente, det in base_mes.groupby("id_cliente"):
        caminho = saida / f"prestacao_{cliente}_{mes}.xlsx"
        detalhe = det[["id_pagamento", "id_devedor", "data_pagamento", "valor_pago"]].sort_values("data_pagamento")
        with pd.ExcelWriter(caminho, **opcoes) as w:
            _escrever_aba(w, fech[fech["id_cliente"] == cliente], "Resumo", moeda_resumo, pct_resumo)
            _escrever_aba(w, detalhe, "Detalhe", ("valor_pago",), coluna_total="valor_pago")
        arquivos.append(caminho)

    caminho = saida / f"conferencia_{mes}.xlsx"
    with pd.ExcelWriter(caminho, **opcoes) as w:
        _escrever_aba(w, fech, "Fechamento", moeda_resumo, pct_resumo)
        _escrever_aba(w, controle, "Controle")
        _escrever_aba(w, erros, "Erros", ("valor_pago",))
    arquivos.append(caminho)
    return arquivos


def montar_controle(pagamentos, erros, base_mes, fech, totais) -> pd.DataFrame:
    itens = [
        ("Linhas de pagamentos lidas", len(pagamentos)),
        ("Linhas com problema (ver aba Erros)", len(erros)),
        ("Pagamentos válidos no mês", len(base_mes)),
        ("Total da base do mês", totais["total_base_mes"]),
        ("Total fechado (soma dos clientes)", totais["total_fechado"]),
        ("Total de honorários", round(fech["honorario"].sum(), 2)),
        ("Total a repassar aos clientes", round(fech["repasse_cliente"].sum(), 2)),
    ]
    for erro, qtd in erros["erro"].value_counts().items():
        itens.append((f"Erros: {erro}", int(qtd)))
    return pd.DataFrame(itens, columns=["item", "valor"])


# ------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fechamento mensal de valores recuperados e honorários.")
    ap.add_argument("--mes", help="Mês de fechamento (AAAA-MM). Padrão: último mês com pagamentos válidos.")
    ap.add_argument("--dados", default="data", help="Pasta com carteira.csv e pagamentos.csv")
    ap.add_argument("--regras", default="config/regras_honorarios.csv")
    ap.add_argument("--saida", default="saida")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    carteira, pagamentos, regras = carregar_dados(args.dados, args.regras)
    log.info("Lidos %d devedores e %d pagamentos", len(carteira), len(pagamentos))

    erros = validar_pagamentos(pagamentos, carteira, regras)
    if len(erros):
        log.warning("%d linhas com problema: %s", len(erros), erros["erro"].value_counts().to_dict())

    limpo = limpar_pagamentos(pagamentos, carteira, regras)
    mes = args.mes or limpo["mes"].max()
    base_mes = limpo[limpo["mes"] == mes]
    if base_mes.empty:
        log.error("Nenhum pagamento válido em %s", mes)
        return 1

    fech = fechar_por_cliente(base_mes, carteira, regras, mes)
    totais = conferir_totais(base_mes, fech)
    controle = montar_controle(pagamentos, erros, base_mes, fech, totais)
    arquivos = exportar_planilhas(fech, base_mes, erros, controle, mes, args.saida)

    log.info("Fechamento de %s concluído. %d arquivos em '%s'", mes, len(arquivos), args.saida)
    print()
    print(fech[["id_cliente", "recuperado", "honorario", "repasse_cliente", "perc_efetivo", "taxa_recuperacao"]]
          .to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
