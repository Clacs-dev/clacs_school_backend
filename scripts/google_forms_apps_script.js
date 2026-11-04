/**
 * CLACS Escola — envia cada resposta do Google Forms de pré-inscrição para o sistema.
 *
 * Instalação (uma vez):
 *  1. No Google Forms da pré-inscrição: ⋮ (Mais) → Editor de scripts (Apps Script).
 *  2. Apague o conteúdo e cole este ficheiro inteiro. Confirme URL_CLACS e SEGREDO abaixo.
 *  3. Escolha a função «instalar» e carregue em ▶ Executar; autorize o acesso pedido pela Google.
 *  4. (Opcional) Execute «enviarRespostasAntigas» para enviar as respostas que já existiam.
 *
 * O sistema reconhece as perguntas pelo título (ex.: «Nome completo do aluno», «Data de nascimento», «Sexo»,
 * «Classe pretendida», «Nome do encarregado de educação», «Parentesco», «Telefone / WhatsApp», «E-mail»).
 * Reenviar uma resposta não cria uma candidatura repetida.
 */
var URL_CLACS = '{{URL}}';
var SEGREDO = '{{SEGREDO}}';

function enviar_(resposta) {
  var respostas = {};
  resposta.getItemResponses().forEach(function (r) {
    respostas[r.getItem().getTitle()] = r.getResponse();
  });
  try {
    var email = resposta.getRespondentEmail();
    if (email && !respostas['E-mail']) respostas['E-mail'] = email;
  } catch (e) { /* o formulário não recolhe o e-mail */ }
  var res = UrlFetchApp.fetch(URL_CLACS, {
    method: 'post',
    contentType: 'application/json',
    headers: { 'X-Clacs-Segredo': SEGREDO },
    payload: JSON.stringify({ id_resposta: resposta.getId(), respostas: respostas }),
    muteHttpExceptions: true,
  });
  if (res.getResponseCode() >= 300) {
    throw new Error('CLACS respondeu ' + res.getResponseCode() + ': ' + res.getContentText());
  }
  return JSON.parse(res.getContentText());
}

/** Chamada pelo Google a cada nova resposta (activador criado por «instalar»). */
function aoSubmeter(e) {
  enviar_(e.response);
}

function instalar() {
  var form = FormApp.getActiveForm();
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'aoSubmeter') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('aoSubmeter').forForm(form).onFormSubmit().create();
  Logger.log('Pronto: as novas respostas passam a chegar ao CLACS Escola.');
}

function enviarRespostasAntigas() {
  var n = 0;
  FormApp.getActiveForm().getResponses().forEach(function (r) {
    var out = enviar_(r);
    if (!out.duplicada) n++;
  });
  Logger.log(n + ' candidatura(s) nova(s) criada(s).');
}
