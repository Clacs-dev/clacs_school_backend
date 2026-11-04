# Cobertura do levantamento de requisitos pela API

Fonte: «Levantamento de Requisitos — Colégio Talatona Kids», versão 1.0 de 03/11/2026 (CLACS Tecnologia).
Uma linha por requisito das secções 4 a 16. Estado: **sim** (a API cumpre), **parcial** (cumpre em parte; conta
meio), **não**, **fora** (infra-estrutura, ecrãs ou decisões da escola: não entra na conta da API).

Contagem: `python -m scripts.cobertura_requisitos` (lê esta tabela).

| Id | Requisito | Estado | Onde / o que falta |
|---|---|---|---|
| 4.2.1 | Estados da candidatura (submetida, em análise, reunião, aprovada, vaga atribuída, documentos pendentes, concluída…) | parcial | `/api/candidaturas`: 9 etapas; falta «aguarda informação» |
| 4.2.2 | Data, responsável, decisão e observações em cada etapa | sim | `candidatura.historico` |
| 4.2.3 | Vagas por nível, classe e turma, sem aprovação automática | sim | detalhe da candidatura → `vagas` |
| 4.2.4 | Sem matrícula final antes da aprovação pedagógica | sim | matricular exige validada + autorizada |
| 4.2.5 | Anexos, validação documental e retoma de formulários incompletos | sim | `/api/publico/candidaturas/{token}` |
| 4.2.6 | Notificar família, secretaria, Direcção e coordenação nas mudanças de estado | sim | alertas «candidatura» + mensagens |
| 4.2.7 | Admissões durante o ano e transferências | parcial | candidatura para qualquer ano; falta regra própria de transferência de entrada |
| 4.2.8 | Listas administrativas e pedagógicas a partir do cadastro | sim | `/api/turmas`, `/api/matriculas`, exportações |
| 4.2.9 | Atribuição da turma e excepções com motivo e aprovador | não | turma cheia bloqueia; falta excepção autorizada |
| 4.3.1 | Renovação recupera o cadastro e pede confirmação/actualização | sim | `/api/portal/educandos/{id}/ficha` + pedido `matricula` com `ficha`, aplicada ao aprovar |
| 4.3.2 | Pai, mãe e encarregado como contactos distintos | sim | `aluno_encarregado.parentesco` |
| 4.3.3 | Alterar o encarregado não apaga os restantes contactos | sim | ligações independentes |
| 4.3.4 | Vários e-mails e telefones, relação com o aluno e preferências | parcial | vários contactos com telefone, WhatsApp e e-mail; preferências por canal |
| 4.3.5 | Sinalizar contactos em falta, duplicados ou inválidos | sim | `/api/contactos/avisos`, `avisos` na ficha |
| 4.3.6 | Documentos obrigatórios por ano lectivo, classe e situação | parcial | `/api/documentos-exigidos` por classe; não por ano lectivo nem situação |
| 4.4.1 | Atribuição por utilizador autorizado com regras de classe e capacidade | sim | matrículas e mudança de turma |
| 4.4.2 | Necessidades de acessibilidade ou outras restrições | parcial | `aluno.necessidades_especiais` |
| 4.4.3 | A idade gera alerta, sem determinar a classe | não | |
| 4.4.4 | Turma sem vaga exige excepção autorizada, com motivo e auditoria | não | |
| 4.4.5 | Critério de distribuição entre turmas | parcial | enche A, depois B; `/api/turmas/sugestao` |
| 5.1.1 | Identificação, ano, classe, turma e estado da matrícula | sim | `/api/alunos/{id}/360` |
| 5.1.2 | Contactos autorizados por assunto (geral, pedagógico, financeiro, urgente) | parcial | principal, responsável financeiro, autorizado a recolher |
| 5.1.3 | Documentos com estado de validação e histórico por ano | parcial | estado de validação; sem histórico por ano |
| 5.1.4 | Histórico de matrículas, turmas, resultados e ocorrências | sim | visão 360°, histórico de turma |
| 5.1.5 | Saúde com acesso especialmente restrito | parcial | campos de saúde; sem restrição própria |
| 5.2.1 | Arquivo por aluno, tipo documental e ano lectivo | parcial | por aluno e tipo |
| 5.2.2 | Estados: solicitado, recebido, em validação, aceite, rejeitado | sim | recebido/aceite/rejeitado; os exigidos não entregues aparecem «em falta» |
| 5.2.3 | Pesquisa e consulta por utilizadores autorizados | parcial | documentos dentro do aluno |
| 5.2.4 | Anexos a candidatura, matrícula, declarações, pedidos e ocorrências | sim | candidatura, pedidos (`/portal/pedidos/{id}/anexos`), ocorrências |
| 5.2.5 | Listas de documentos em falta e notificações | sim | `/api/documentos/em-falta` e `/avisar` |
| 5.2.6 | Política de armazenamento, cópias de segurança, retenção | fora | infra-estrutura |
| 6.1.1 | Vista consolidada facturado/recebido/pendente/dívida | sim | `/api/cobrancas` (resumo), painel |
| 6.1.2 | Consulta por dia, semana, mês, ano lectivo, turma, aluno e serviço | parcial | por competência, turma, aluno, serviço; não por dia/semana |
| 6.1.3 | Propina, alimentação, refeitório e serviços adicionais | sim | serviços e adesões |
| 6.1.4 | Indicadores com detalhe por aluno, encarregado e movimento | sim | |
| 6.1.5 | Referências, facturas, pagamentos e gateway | sim | referência (simulada), Primavera |
| 6.1.6 | Pagamentos por aluno, período e serviço, parciais e excepções | sim | alocação de pagamentos |
| 6.1.7 | Dívidas anteriores, multas, descontos e acordos | sim | acordos de pagamento (`/api/acordos`) |
| 6.2.1 | Calendário e vencimento por serviço e ano lectivo | parcial | dia de vencimento e plano |
| 6.2.2 | Multas segundo a regra aprovada (por escalões) | sim | `multa_regra.escaloes` por dias de atraso (valores a confirmar pela escola) |
| 6.2.3 | Sem aplicação duplicada nos recálculos | sim | `aplicar_multas` só sobe |
| 6.2.4 | Memória de cálculo (base, percentagem, data, valor) | sim | `cobranca.multa_calculo`: base, %, escalão, dias de atraso, data, valor |
| 6.2.5 | Transportar saldos anteriores | parcial | cobranças em aberto continuam |
| 6.2.6 | Excepções autorizadas com justificação e histórico | sim | `/api/cobrancas/{id}/isentar-multa` (Direcção, motivo, auditoria) |
| 6.2.7 | Listas de pendentes e lembretes configuráveis | sim | devedores, regras |
| 6.3.1 | Lembretes antes ou perto do vencimento | sim | regras automáticas |
| 6.3.2 | Listas de incumprimento a partir do dia 11 | sim | devedores |
| 6.3.3 | Cadências configuráveis (3, 5, 10 dias) | sim | regras |
| 6.3.4 | Valor, prazo, saldo e instrução de pagamento na mensagem | sim | modelos com variáveis |
| 6.3.5 | Histórico de notificações e estado de entrega | sim | `mensagem.estado` |
| 6.4.1 | Elegibilidade, percentagem, beneficiário, datas e prioridade | parcial | descontos com validade |
| 6.4.2 | Descontos excepcionais com motivo e aprovador | parcial | `aprovado_por`, sem motivo |
| 6.4.3 | Histórico e sem recálculo retroactivo | parcial | desconto gravado na cobrança |
| 6.4.4 | Desconto no extracto e relatórios | sim | |
| 6.5.1 | Modelos e acordos de pagamento personalizados | sim | `/api/acordos-modelos` e acordos personalizados |
| 6.5.2 | Acordo associado ao aluno/encarregado | sim | acordo ligado ao encarregado (e ao aluno) |
| 6.5.3 | Prestações, montantes, datas e estados | sim | prestações com valor, vencimento e estado |
| 6.5.4 | Pagamentos parciais, atrasos e saldo do acordo | sim | pago, saldo e prestações atrasadas a partir dos pagamentos às cobranças |
| 6.5.5 | Efeito do acordo sobre multas e incumprimento | sim | suspende multas; incumprimento quando há prestação vencida |
| 6.5.6 | Aprovação, motivo e alterações do acordo | sim | aprovação pela Direcção, motivo, alteração do plano no histórico |
| 6.6.1 | Pesquisar encarregado e ver todos os educandos | sim | |
| 6.6.2 | Saldo por aluno e resumo familiar informativo | parcial | |
| 6.6.3 | Não misturar contas individuais | sim | |
| 6.6.4 | Pagamentos sem referência, duplicados, devolvidos ou mal associados | parcial | comprovativos, webhook idempotente |
| 7.1 | Portal: pré-inscrição, matrícula, renovação e actualização de contactos | sim | pré-inscrição (Forms), matrícula, renovação com ficha e contactos |
| 7.2 | Portal: consulta e carregamento de documentos | sim | `/api/portal/educandos/{id}/documentos` |
| 7.3 | Portal: propinas, pagamentos, saldos e referências | sim | |
| 7.4 | Portal: notas, pautas e informação pedagógica | sim | |
| 7.5 | Portal: pedidos de declarações e serviços | sim | |
| 7.6 | Portal: justificação de faltas com comprovativo e saída antecipada | sim | anexos ao pedido; tipo `saida_antecipada` |
| 7.7 | Portal: calendário, horários, avisos e estado dos pedidos | sim | `/portal/educandos/{id}/horario`, `/portal/calendario`, mensagens, estado dos pedidos |
| 7.8 | Portal: informação de saúde e ocorrências autorizadas | sim | `/portal/educandos/{id}/saude` e instruções de medicação com receita |
| 7.9 | Valor devido, pago, saldo e acção sem abrir PDF | sim | |
| 7.10 | Registar visualização/entrega | parcial | estado da mensagem |
| 7.11 | Interface mobile-first, linguagem clara, formulários com progresso | fora | ecrãs |
| 8.2.1 | Classificar avisos gerais, pedagógicos, financeiros, individuais, sensíveis | parcial | |
| 8.2.2 | Comunicações associadas ao aluno e aos contactos autorizados | sim | |
| 8.2.3 | Encaminhar pedidos para secretaria, coordenação ou tesouraria | sim | `servico` + `/api/pedidos/{id}/encaminhar` |
| 8.2.4 | Estados: recebido, em análise, aguarda informação, aprovado, rejeitado, concluído | sim | pendente (recebido), em_analise, aguarda_informacao, aprovado, rejeitado, concluido |
| 8.2.5 | Notificar o encarregado das mudanças de estado | sim | mensagem à família em cada passo |
| 8.2.6 | Separar avisos gerais de informação privada | sim | |
| 8.2.7 | WhatsApp Business/API | sim | integração (Meta, Evolution, Z-API) |
| 8.2.8 | Diagnóstico do e-mail (DNS, SPF, DKIM) | fora | infra-estrutura |
| 8.3.1 | Tipos: justificação, saída antecipada, informação, documento, saúde, financeiro, outros | sim | justificação, saída antecipada, informação, declaração, saúde, financeiro, outro |
| 9.1 | Declaração pedida no portal com o aluno e o tipo | sim | pedido com `modelo`; `/api/portal/declaracoes` |
| 9.2 | Modelos configuráveis | sim | `/api/modelos-declaracao` com variáveis validadas |
| 9.3 | Geração a partir do cadastro, sem redigitar | sim | |
| 9.4 | Estados: submetida, em análise, aguarda informação, aprovada, emitida, entregue | sim | recebido → em análise → aguarda informação → aprovado (emitida) → concluído (entregue) |
| 9.5 | Prazos em dias úteis | sim | `prazo` e `fora_do_prazo` em dias úteis (parâmetro prazo_pedidos_dias; sem feriados) |
| 9.6 | Taxa só quando a escola configurar um serviço pago | sim | gratuitas |
| 9.7 | Transferência, desistência e saída | parcial | pedido de transferência |
| 10.2.1 | Ano lectivo, períodos, feriados e eventos | sim | anos e períodos + `/api/eventos` (feriados, interrupções, eventos) |
| 10.2.2 | Turmas, capacidade e alunos | sim | |
| 10.2.3 | Disciplinas, professores, turmas e horários | sim | `/api/horarios` (turma, disciplina, professor, sala, tempo) |
| 10.2.4 | Alterações, substituições e histórico de horários | sim | `/horarios/{id}/alterar` (versões com valido_desde/ate), `/api/substituicoes` |
| 10.2.5 | Vistas para Direcção, coordenação, docentes e famílias | sim | horário por turma e professor, aulas do dia, portal da família |
| 10.3.1 | Critérios, instrumentos e ponderações (por classe/disciplina) | parcial | pesos globais por tipo |
| 10.3.2 | Notas de provas, trabalhos e actividades | sim | |
| 10.3.3 | Fórmulas distintas quando o modelo o exigir | parcial | |
| 10.3.4 | Validar resultados antes da publicação | não | |
| 10.3.5 | Pautas por período e anuais | sim | |
| 10.3.6 | Promoção/retenção, responsáveis e critérios | sim | fecho do ano |
| 10.3.7 | Histórico e alterações restritas após o encerramento | parcial | |
| 10.4.1 | Planificações dos professores e acompanhamento | não | |
| 10.4.2 | Evolução das aprendizagens e dificuldades | parcial | alerta de queda de desempenho |
| 10.4.3 | Observações pedagógicas e medidas de apoio | parcial | registo diário |
| 10.4.4 | Comportamento segundo critérios da escola | sim | registo diário |
| 10.4.5 | Comunicar aspectos relevantes às famílias | sim | |
| 10.4.6 | Responsáveis por consulta, validação e alteração | sim | perfis e permissões |
| 11.1.1 | O professor regista na aula prevista | sim | `/api/aulas` (aulas previstas) e `/api/aulas/registar` |
| 11.1.2 | Registo com aluno, data, hora/tempo, disciplina, turma e professor | sim | `assiduidade.horario_id`: tempo, hora, disciplina, turma e professor da aula |
| 11.1.3 | Falta numa aula não implica as restantes | sim | por disciplina |
| 11.1.4 | Justificação no portal com comprovativo | sim | anexo no pedido de justificação |
| 11.1.5 | Justificação validada pela escola | sim | pedido aprovado |
| 11.1.6 | O registo original permanece; a justificação acrescenta decisão | sim | `assiduidade.estado_original`, `pedido_id`, `justificada_por/em` |
| 11.1.7 | Relatórios por aluno, turma, disciplina e período | sim | `/api/assiduidade/relatorio` por aluno e disciplina |
| 11.1.8 | Regras de atraso, saída antecipada, justificada e injustificada | parcial | |
| 11.2.1 | Integração com cartão de acesso | fora | depende do fabricante |
| 11.3.1 | Assiduidade dos professores, substituições, aulas previstas/realizadas | sim | `/api/professores/assiduidade`: previstas, dadas, faltas, substituições, atrasos |
| 12.2.1 | Ocorrência com data/hora, local, responsável e descrição | sim | `/api/saude/ocorrencias` com data/hora, local, quem registou e descrição |
| 12.2.2 | Sinais, medidas tomadas e encaminhamento | sim | sinais, medidas e encaminhamento |
| 12.2.3 | Contacto à família: quem, quando, canal e orientação | sim | nota `contacto_familia`: quem, canal e orientação |
| 12.2.4 | Recolhido, ficou na escola ou foi encaminhado | sim | `desfecho`: ficou na escola, recolhido, encaminhado |
| 12.2.5 | Relatório cronológico, notas complementares e histórico | sim | notas cronológicas e alterações registadas |
| 12.2.6 | Relatório ao encarregado segundo as regras | sim | `partilhada` → portal e aviso à família |
| 12.3.1 | Instruções de medicação do encarregado, com receita | sim | `/portal/educandos/{id}/medicacao` com receita e confirmação escrita |
| 12.3.2 | Registo da administração (medicamento, dose, hora, responsável) | sim | `/api/saude/medicacao/{id}/administracoes`: quem, quando, dose |
| 12.3.3 | Campos obrigatórios, autorizações e protocolo | sim | campos obrigatórios, autorização prévia, confirmação da família |
| 12.3.4 | Alergias e emergências | parcial | ficha de saúde |
| 12.3.5 | Dados de saúde com acesso estritamente limitado | sim | módulo `saude` restrito (perfil Saúde e Direcção) |
| 12.4.1 | Registar criação, consulta e alteração | sim | auditoria de criação, consulta (`view`) e alteração |
| 12.4.2 | Sem alterações silenciosas a ocorrências encerradas | sim | ocorrência encerrada não se altera; só notas |
| 13.1 | Dashboard da Direcção | sim | `/api/painel` |
| 13.2 | Dashboard da tesouraria | sim | |
| 13.3 | Dashboard da secretaria | sim | `/api/secretaria/painel` |
| 13.4 | Dashboard pedagógico | parcial | |
| 13.5 | Dashboard da comunicação | parcial | |
| 13.6 | Dashboard da saúde | sim | `/api/saude/painel` |
| 13.7 | Filtros, detalhe, relatórios periódicos e exportações | sim | relatórios agendados |
| 14.1 | Orçamento por período, categoria e serviço | sim | `/api/orcamentos` com linhas por categoria e mês |
| 14.2 | Previsão de receitas por alunos e serviços | sim | `/orcamentos/{id}/previsao-receitas` |
| 14.3 | Despesas previstas e recorrentes | sim | `/api/despesas` (recorrente) |
| 14.4 | Orçamento versus realizado | sim | `/orcamentos/{id}/execucao` |
| 14.5 | Desvios e tendências | sim | desvio por categoria e série mensal |
| 16.1 | Acesso por função, turma, aluno e tipo de dado | parcial | professor só nas suas turmas |
| 16.2 | Autenticação segura, recuperação e sessões | sim | 2FA, bloqueio, recuperação |
| 16.3 | Separação de acessos entre perfis | sim | |
| 16.4 | Protecção reforçada dos dados de saúde | parcial | módulo de saúde restrito e consultas auditadas; campos de saúde da ficha ainda visíveis com cadastro |
| 16.5 | Auditoria de finanças, notas, matrículas, descontos e ocorrências | sim | `auditoria_log` |
| 16.6 | Revogação de acessos | sim | utilizador inactivo |
| 16.7 | Backups, retenção, dispositivos e obrigações legais | fora | infra-estrutura |
