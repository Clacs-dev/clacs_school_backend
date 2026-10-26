"""Dados iniciais.

    python -m scripts.seed            perfis, permissões, estabelecimento e administrador (idempotente)
    python -m scripts.seed --demo     + dados de demonstração do protótipo, se a base ainda não tiver alunos
                                      (também activado com a variável SEED_DEMO=true)
"""
import argparse
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common import soma_meses
from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    AdesaoServico, Aluno, AlunoEncarregado, AnoLectivo, Assiduidade, AtribuicaoDocente, Avaliacao, Classe,
    ClasseDisciplina, Cobranca, Desconto, Disciplina, Encarregado, Estabelecimento, Funcionario, IntegracaoConfig,
    IntegracaoDivergencia, IntegracaoSyncLog, Matricula, MetodoPagamento, ModeloMensagem, MultaRegra,
    NivelEnsino, Nota, Parametro, Perfil, PerfilPermissao, PeriodoLectivo, Permissao, PlanoPagamento,
    PreferenciaContacto, RefeicaoTipo, RegistoDiario, RegraAutomacao, Sala, Servico, TabelaPreco,
    TipoAvaliacao, TipoOcorrencia, Turma, Utilizador, UtilizadorPerfil,
)
from app.routers.academico import trimestres
from app.security import ACCOES, MATRIZ, MODULOS, hash_senha
from app.services.financeiro import registar_pagamento
from app.services.indicadores import garantir_padrao as garantir_indicadores

PERFIS = [("admin", "Administrador"), ("direccao", "Direcção"), ("secretaria", "Secretaria"),
          ("financeiro", "Financeiro"), ("coordenacao", "Coordenação pedagógica"), ("professor", "Professor"),
          ("encarregado", "Encarregado de educação")]


def base(db: Session) -> Estabelecimento:
    perfis = {p.codigo: p for p in db.scalars(select(Perfil))}
    perfis_novos = set()
    for codigo, nome in PERFIS:
        if codigo not in perfis:
            perfis[codigo] = Perfil(codigo=codigo, nome=nome)
            db.add(perfis[codigo])
            perfis_novos.add(codigo)
    perms = {(p.modulo, p.accao): p for p in db.scalars(select(Permissao))}
    perms_novas = set()
    for m in MODULOS:
        for a in ACCOES:
            if (m, a) not in perms:
                perms[(m, a)] = Permissao(modulo=m, accao=a)
                db.add(perms[(m, a)])
                perms_novas.add((m, a))
    db.flush()
    # A matriz só se aplica a perfis e permissões acabados de criar (ex.: um módulo novo): as permissões que o
    # administrador retirou em "Perfis e permissões" não voltam a aparecer em cada arranque. O admin tem sempre tudo.
    existentes = set(db.execute(select(PerfilPermissao.perfil_id, PerfilPermissao.permissao_id)).tuples())
    for codigo, modulos in MATRIZ.items():
        for m, accoes in modulos.items():
            for a in accoes:
                if codigo != "admin" and codigo not in perfis_novos and (m, a) not in perms_novas:
                    continue
                par = (perfis[codigo].id, perms[(m, a)].id)
                if par not in existentes:
                    db.add(PerfilPermissao(perfil_id=par[0], permissao_id=par[1]))

    est = db.scalar(select(Estabelecimento).order_by(Estabelecimento.id).limit(1))
    if not est:
        est = Estabelecimento(nome="Talatona Kids", morada="Luanda, Angola", email="geral@clacs.ao")
        db.add(est)
        db.flush()
        db.add(Parametro(estabelecimento_id=est.id, chave="dia_vencimento", valor=10,
                         descricao="Dia do mês em que vencem as propinas"))
        db.add(Parametro(estabelecimento_id=est.id, chave="moeda", valor="AOA", descricao="Kwanza"))

    garantir_indicadores(db, est.id)

    s = get_settings()
    admin = db.scalar(select(Utilizador).where(func.lower(Utilizador.email) == s.admin_email.lower()))
    if not admin:
        admin = Utilizador(nome="Administrador", email=s.admin_email.lower(), senha_hash=hash_senha(s.admin_password))
        db.add(admin)
        db.flush()
        db.add(UtilizadorPerfil(utilizador_id=admin.id, perfil_id=perfis["admin"].id, estabelecimento_id=est.id))
    db.commit()
    return est


# ---------------------------------------------------------------- demonstração (dados do protótipo)
NOMES = ["Ana Cota", "Bento Lima", "Carla Neto", "Diogo Pires", "Eva Santos", "Fábio Gomes", "Graça Luís",
         "Hélder Dias", "Inês Mateus", "João Paulo", "Kiala Sebastião", "Laura Pinto", "Mário Fonseca",
         "Nádia Costa", "Osvaldo Reis", "Paula Tavares", "Quintino Alves", "Rita Domingos", "Samuel Cardoso",
         "Tânia Varela"]
PAIS = ["Maria", "José", "Teresa", "António", "Rosa", "Manuel", "Isabel", "Pedro", "Luísa", "Francisco"]
TURMAS = [("5.ª A", 5), ("6.ª B", 6), ("7.ª A", 7), ("7.ª B", 7), ("8.ª A", 8)]
DIRECTORES = ["Marta Silva", "Pedro Quim", "Joana Ndala", "Rui Cassoma", "Luísa Baptista"]
REF_MODO = ["alimentacao", "refeitorio", None]
COMPORTAMENTO = ["bom", "bom", "regular", "bom", "atencao"]


def demo(db: Session, est: Estabelecimento, hoje: date | None = None) -> bool:
    if db.scalar(select(func.count()).select_from(Aluno).where(Aluno.estabelecimento_id == est.id)):
        return False
    hoje = hoje or date.today()
    E = est.id
    y = hoje.year if hoje.month >= 9 else hoje.year - 1
    ano_ant = AnoLectivo(estabelecimento_id=E, designacao=f"{y - 1}/{y}", data_inicio=date(y - 1, 9, 1),
                         data_fim=date(y, 7, 15), estado="encerrado")
    ano = AnoLectivo(estabelecimento_id=E, designacao=f"{y}/{y + 1}", data_inicio=date(y, 9, 1),
                     data_fim=date(y + 1, 7, 15), estado="activo")
    db.add_all([ano_ant, ano])
    db.flush()
    periodos = trimestres(ano)
    db.add_all(periodos + trimestres(ano_ant))

    primario = NivelEnsino(estabelecimento_id=E, nome="Ensino Primário", ordem=1)
    ciclo1 = NivelEnsino(estabelecimento_id=E, nome="I Ciclo do Ensino Secundário", ordem=2)
    db.add_all([primario, ciclo1])
    db.flush()
    classes = {n: Classe(nivel_id=(primario if n <= 6 else ciclo1).id, designacao=f"{n}.ª Classe", ordem=n)
               for n in (5, 6, 7, 8)}
    db.add_all(classes.values())
    discs = [Disciplina(estabelecimento_id=E, nome=n, sigla=s) for n, s in
             [("Língua Portuguesa", "LP"), ("Matemática", "MAT"), ("Inglês", "ING"), ("Ciências da Natureza", "CN"),
              ("História", "HIS"), ("Geografia", "GEO"), ("Educação Física", "EF")]]
    db.add_all(discs)
    db.flush()
    for c in classes.values():
        for d in discs:
            db.add(ClasseDisciplina(classe_id=c.id, disciplina_id=d.id, carga_horaria=4 if d.sigla in ("LP", "MAT") else 2))

    turmas = []
    for i, (nome, n) in enumerate(TURMAS):
        sala = Sala(estabelecimento_id=E, designacao=f"Sala {i + 1}", capacidade=30)
        prof = Funcionario(estabelecimento_id=E, nome_completo=DIRECTORES[i], cargo="professor")
        db.add_all([sala, prof])
        db.flush()
        t = Turma(estabelecimento_id=E, ano_id=ano.id, classe_id=classes[n].id, designacao=nome, sala_id=sala.id,
                  periodo_dia="manha" if i < 3 else "tarde", director_turma_id=prof.id, capacidade=30)
        turmas.append(t)
    db.add_all(turmas)
    db.add(Funcionario(estabelecimento_id=E, nome_completo="Direcção Geral", cargo="director"))

    # Catálogo financeiro
    sv = {c: Servico(estabelecimento_id=E, codigo=c, nome=n, categoria=cat, periodicidade=per, artigo_primavera=art)
          for c, n, cat, per, art in [("PROP", "Propina", "propina", "mensal", "SRV-PROP"),
                                      ("ALIM", "Alimentação", "alimentacao", "mensal", "SRV-ALIM"),
                                      ("REF", "Refeitório (utilização)", "refeitorio", "mensal", "SRV-REF"),
                                      ("MAT", "Matrícula", "matricula", "unica", "SRV-MAT"),
                                      ("FARD", "Fardamento", "fardamento", "unica", "SRV-FARD")]}
    db.add_all(sv.values())
    db.flush()
    for c, v in [("PROP", 45000), ("ALIM", 18000), ("REF", 8000), ("MAT", 25000), ("FARD", 15000)]:
        db.add(TabelaPreco(estabelecimento_id=E, servico_id=sv[c].id, ano_id=ano.id, valor=Decimal(v)))
    db.add(PlanoPagamento(estabelecimento_id=E, designacao="10 prestações, dia 10", num_prestacoes=10,
                          mes_inicio=9, dia_vencimento=10))
    db.add(MultaRegra(estabelecimento_id=E, servico_id=sv["PROP"].id, dias_tolerancia=15, tipo="percentual",
                      valor=Decimal(10), maximo=Decimal(10000)))
    db.add(Desconto(estabelecimento_id=E, designacao="Desconto de irmãos", origem="irmaos", tipo="percentual",
                    valor=Decimal(10), servico_id=sv["PROP"].id))
    metodos = [MetodoPagamento(estabelecimento_id=E, nome=n, tipo=t) for n, t in
               [("Numerário", "numerario"), ("Transferência bancária", "transferencia"),
                ("Multicaixa Express", "multicaixa"), ("TPA", "tpa")]]
    db.add_all(metodos)
    db.add_all([RefeicaoTipo(estabelecimento_id=E, designacao="Lanche", hora_inicio=time(10, 0)),
                RefeicaoTipo(estabelecimento_id=E, designacao="Almoço", hora_inicio=time(12, 30))])
    tipos = [TipoAvaliacao(estabelecimento_id=E, designacao=n, peso=Decimal(p)) for n, p in
             [("MAC", 1), ("Prova do professor", 1), ("Prova trimestral", 2)]]
    db.add_all(tipos)
    db.add_all([TipoOcorrencia(estabelecimento_id=E, designacao=n, gravidade=g) for n, g in
                [("Elogio", "positiva"), ("Atraso reiterado", "leve"), ("Indisciplina", "moderada"),
                 ("Agressão", "grave")]])
    db.flush()

    # Alunos, encarregados, matrículas e serviços
    alunos, matriculas, ees = [], [], []
    for i, nome in enumerate(NOMES):
        apelido = nome.split()[-1]
        ee = Encarregado(estabelecimento_id=E, nome_completo=f"{PAIS[i % 10]} {apelido}",
                         nif=str(5400000000 + i * 13579), telefone=f"+244 92{3000000 + i * 7311}",
                         whatsapp=f"+244 92{3000000 + i * 7311}", email=f"ee{i + 1}@exemplo.ao")
        a = Aluno(estabelecimento_id=E, numero_processo=f"{y}{i + 1:04d}", nome_completo=nome,
                  data_nascimento=date(y - 10 - (i % 4), 1 + i % 12, 1 + i % 27), sexo="F" if nome.split()[0][-1] == "a" else "M")
        db.add_all([ee, a])
        db.flush()
        for canal in ("whatsapp", "sms", "email", "app"):
            db.add(PreferenciaContacto(encarregado_id=ee.id, canal=canal))
        db.add(AlunoEncarregado(aluno_id=a.id, encarregado_id=ee.id, parentesco="mae" if i % 2 == 0 else "pai",
                                principal=True, responsavel_financeiro=True))
        t = turmas[i % 5]
        m = Matricula(estabelecimento_id=E, aluno_id=a.id, ano_id=ano.id, turma_id=t.id,
                      tipo="renovacao" if i >= 5 else "nova", data_matricula=date(y, 8, 10 + i % 15))
        db.add(m)
        db.flush()
        modo = REF_MODO[i % 3]
        if modo:
            db.add(AdesaoServico(matricula_id=m.id, servico_id=sv["ALIM" if modo == "alimentacao" else "REF"].id,
                                 modalidade="alimentacao_completa" if modo == "alimentacao" else "so_utilizacao",
                                 data_inicio=date(y, 9, 1)))
        alunos.append(a)
        matriculas.append(m)
        ees.append(ee)
    db.flush()

    # Cobranças do mês anterior e do mês corrente
    mes = hoje.replace(day=1)
    meses = [soma_meses(mes, -1), mes] if mes > date(y, 9, 1) else [mes]
    for comp in meses:
        for i, (a, m, ee) in enumerate(zip(alunos, matriculas, ees)):
            servs = [sv["PROP"]]
            if REF_MODO[i % 3]:
                servs.append(sv["ALIM" if REF_MODO[i % 3] == "alimentacao" else "REF"])
            for s in servs:
                valor = db.scalar(select(TabelaPreco.valor).where(TabelaPreco.servico_id == s.id))
                c = Cobranca(estabelecimento_id=E, matricula_id=m.id, aluno_id=a.id, encarregado_id=ee.id,
                             servico_id=s.id, competencia=comp, descricao=f"{s.nome} {comp:%m/%Y}",
                             valor_base=valor, vencimento=comp.replace(day=10))
                db.add(c)
                db.flush()
                id_ = i + 1
                em_divida = (s.codigo == "PROP" and id_ % 4 == 0) or \
                            (s.codigo != "PROP" and id_ % 5 == 1 and comp == mes)
                if not em_divida:
                    pago_em = min(hoje, comp.replace(day=5 + i % 5))
                    db.commit()
                    registar_pagamento(db, E, None, metodo_id=metodos[i % 4].id, valor=valor, cobranca_ids=[c.id],
                                       data_pagamento=pago_em, referencia=f"REF{comp:%y%m}{id_:03d}")
    db.flush()

    # Pedagógico: duas avaliações no 1.º trimestre (LP, MAT, ING) por turma
    p1 = periodos[0]
    d1, d2 = max(p1.data_inicio, hoje - timedelta(days=20)), max(p1.data_inicio, hoje - timedelta(days=2))
    for t in turmas:
        for d in discs[:3]:
            av1 = Avaliacao(turma_id=t.id, disciplina_id=d.id, periodo_id=p1.id, tipo_id=tipos[0].id, data=d1, descricao="MAC")
            av2 = Avaliacao(turma_id=t.id, disciplina_id=d.id, periodo_id=p1.id, tipo_id=tipos[1].id, data=d2,
                            descricao="Prova do professor")
            db.add_all([av1, av2])
            db.flush()
            for i, a in enumerate(alunos):
                if matriculas[i].turma_id != t.id:
                    continue
                med = 5 + ((i * 7) % 9 + (i % 3) / 2) / 2  # escala de 0 a 10
                delta = 0.5 if i == 4 else 0  # Eva Santos desce 1 valor
                db.add(Nota(avaliacao_id=av1.id, aluno_id=a.id, valor=Decimal(str(min(10, med + delta)))))
                db.add(Nota(avaliacao_id=av2.id, aluno_id=a.id, valor=Decimal(str(max(0, med - delta)))))
    for i, a in enumerate(alunos):
        t = matriculas[i].turma_id
        faltas = (i * 3) % 9
        for k in range(faltas):
            db.add(Assiduidade(aluno_id=a.id, turma_id=t, data=hoje - timedelta(days=3 + 2 * k),
                               estado="falta_injustificada" if k < 3 else "falta_justificada"))
        db.add(RegistoDiario(turma_id=t, aluno_id=a.id, data=hoje - timedelta(days=1),
                             comportamento=COMPORTAMENTO[i % 5]))

    # Comunicação e regras (ecrã "Comunicação e regras" do protótipo)
    modelo = ModeloMensagem(estabelecimento_id=E, codigo="antes_vencimento", canal="whatsapp",
                            corpo="Caro encarregado, a propina de {aluno} vence a {data}. Valor: {valor}.",
                            variaveis=["aluno", "data", "valor", "servico"])
    db.add(modelo)
    db.flush()
    for nome, evento, dias, canais, destino, activa in [
        ("Lembrete 3 dias antes do dia 10", "antes_vencimento", 3, ["whatsapp"], "encarregado", True),
        ("Aviso 3 dias após o vencimento", "apos_vencimento", 3, ["whatsapp", "sms"], "encarregado", True),
        ("Segundo aviso 10 dias após o vencimento", "apos_vencimento", 10, ["email"], "encarregado", False),
        ("Alerta à Direcção: dívida acima de 30 dias", "divida_acima_dias", 30, ["app"], "direccao", True)]:
        db.add(RegraAutomacao(estabelecimento_id=E, nome=nome, evento=evento, dias_offset=dias, canais=canais,
                              destino=destino, activa=activa,
                              modelo_id=modelo.id if evento == "antes_vencimento" else None))

    # Contas de demonstração: um professor (só vê a 5.ª A) e um encarregado (portal), com a senha do administrador.
    perfis = {pf.codigo: pf.id for pf in db.scalars(select(Perfil))}
    prof = db.scalar(select(Funcionario).where(Funcionario.estabelecimento_id == E, Funcionario.nome_completo == DIRECTORES[0]))
    for d in discs[:3]:
        db.add(AtribuicaoDocente(turma_id=turmas[0].id, disciplina_id=d.id, funcionario_id=prof.id))
    senha = hash_senha(get_settings().admin_password)
    for email, nome, perfil, extra in [("professor@clacs.ao", DIRECTORES[0], "professor", {"funcionario_id": prof.id}),
                                       ("encarregado@clacs.ao", ees[0].nome_completo, "encarregado", {"encarregado_id": ees[0].id})]:
        if not db.scalar(select(Utilizador.id).where(Utilizador.email == email)):
            u = Utilizador(nome=nome, email=email, senha_hash=senha, **extra)
            db.add(u)
            db.flush()
            db.add(UtilizadorPerfil(utilizador_id=u.id, perfil_id=perfis[perfil], estabelecimento_id=E))
    db.add(Parametro(estabelecimento_id=E, chave="nota_maxima", valor=10, descricao="Nota máxima da escala (10 ou 20)"))
    db.add(Parametro(estabelecimento_id=E, chave="nota_minima", valor=5, descricao="Média mínima para aprovar"))

    # Primavera (simulado)
    db.add(IntegracaoConfig(estabelecimento_id=E, sistema="primavera", activo=False))
    agora = datetime.now(timezone.utc)
    db.add(IntegracaoSyncLog(estabelecimento_id=E, entidade="documento", direccao="recepcao", estado="ok",
                             total_registos=86, iniciado_em=agora - timedelta(minutes=12),
                             terminado_em=agora - timedelta(minutes=11), detalhe={"simulado": True}))
    cob_divida = db.scalar(select(Cobranca).where(Cobranca.estabelecimento_id == E, Cobranca.estado == "pendente")
                           .order_by(Cobranca.id).limit(1))
    db.add(IntegracaoDivergencia(estabelecimento_id=E, entidade="cobranca", id_local=cob_divida.id if cob_divida else None,
                                 id_externo=f"FT {y}/412", campo="estado", valor_local="pendente",
                                 valor_externo="paga", descricao="Pago no Primavera, pendente na escola"))
    db.add(IntegracaoDivergencia(estabelecimento_id=E, entidade="cliente", id_local=ees[-1].id,
                                 id_externo="C-0219", campo="nif", valor_local=ees[-1].nif,
                                 valor_externo="5400999999", descricao="NIF diferente entre os sistemas"))
    db.commit()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="carrega os dados de demonstração do protótipo")
    args = ap.parse_args()
    with SessionLocal() as db:
        est = base(db)
        print(f"Base pronta: {est.nome} (id {est.id})")
        if args.demo or os.environ.get("SEED_DEMO", "").lower() == "true":
            print("Dados de demonstração carregados." if demo(db, est) else "Já existem alunos; demonstração ignorada.")


if __name__ == "__main__":
    main()
