import streamlit as st
import pandas as pd
import os
import json
import hashlib
import secrets
from sqlalchemy import create_engine, text
from datetime import datetime, time, timedelta
from io import BytesIO
from fpdf import FPDF
import time as time_module
import requests
import re
import streamlit.components.v1 as components

# --- FUNÇÃO PARA PUXAR O TOPO PARA CIMA (ELIMINA O ESPAÇO VAZIO) ---
def puxar_topo_para_cima():
    components.html("""
        <script>
            const doc = window.parent.document;
            const target = doc.querySelector('.main .block-container');
            if (target) {
                target.style.paddingTop = '0px';
                target.style.marginTop = '0px';
            }
            const header = doc.querySelector('header[data-testid="stHeader"]');
            if (header) {
                header.style.minHeight = '30px';
                header.style.height = '30px';
            }
        </script>
    """, height=0)

# --- MÓDULO DE SEGURANÇA AVANÇADA (PBKDF2-HMAC-SHA256 COM SALT) ---
def gerar_hash_senha(senha_pura: str) -> str:
    """Gera um hash PBKDF2 HMAC SHA-256 com 120.000 iterações (resistente a força bruta em GPU)."""
    salt = secrets.token_hex(16)
    kdf = hashlib.pbkdf2_hmac(
        'sha256',
        senha_pura.encode('utf-8'),
        salt.encode('utf-8'),
        120000
    ).hex()
    return f"pbkdf2_sha256$120000${salt}${kdf}"

def verificar_senha(senha_pura: str, hash_armazenado: str) -> bool:
    """Valida a senha suportando PBKDF2 e mantendo compatibilidade retroativa com hashes legados e texto plano."""
    if not hash_armazenado:
        return False
    
    if hash_armazenado.startswith("pbkdf2_sha256$"):
        try:
            _, iteracoes, salt, hash_esperado = hash_armazenado.split("$", 3)
            kdf = hashlib.pbkdf2_hmac(
                'sha256',
                senha_pura.encode('utf-8'),
                salt.encode('utf-8'),
                int(iteracoes)
            ).hex()
            return secrets.compare_digest(kdf, hash_esperado)
        except Exception:
            return False

    if "$" in hash_armazenado:
        try:
            salt, hash_esperado = hash_armazenado.split("$", 1)
            hash_calculado = hashlib.sha256((salt + senha_pura).encode('utf-8')).hexdigest()
            return secrets.compare_digest(hash_calculado, hash_esperado)
        except Exception:
            return False

    return secrets.compare_digest(senha_pura, hash_armazenado)

# --- INTEGRAÇÃO LLAMA 3 VIA GROQ + LANGCHAIN + BUSCA WEB ---
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from duckduckgo_search import DDGS

def formatar_acao_infinitivo(texto_bruto):
    """Converte textos passados/relatórios do banco para recomendações no infinitivo."""
    txt = texto_bruto.strip()
    
    substituicoes = [
        (r"(?i)^foi realizada a troca d[eo]s?\s*", "Realizar a troca do "),
        (r"(?i)^foi realizada a?\s*", "Realizar "),
        (r"(?i)^foi feito a?\s*", "Efetuar "),
        (r"(?i)^foi trocado a?\s*", "Trocar "),
        (r"(?i)^foi identificado que\s*", "Verificar "),
        (r"(?i)^foi constatado que\s*", "Inspecionar "),
        (r"(?i)^trocar\s*", "Trocar "),
        (r"(?i)^realizada a?\s*", "Realizar "),
    ]
    
    for padrao, subst in substituicoes:
        if re.search(padrao, txt):
            txt = re.sub(padrao, subst, txt).strip()
            break
            
    if txt.lower().startswith("foi "):
        txt = "Verificar " + txt[4:]
        
    return txt.rstrip('.')
    
# --- 1. CONFIGURAÇÕES E ESTILOS ---
NOME_SISTEMA = "Updated Yesterday"
SLOGAN = "Seu controle. Nossa prioridade."
LOGO_URL = "https://i.postimg.cc/rwQs1cpc/Design-sem-nome-(2).png"
ORDEM_AREAS = ["Motorista", "Borracharia", "Mecânica", "Elétrica", "Chapeamento", "Limpeza"]
LISTA_TURNOS = ["Não definido", "Dia", "Noite"]
LISTA_TIPOS_OS = ["Preventiva", "Corretiva", "Preditiva", "Checklist", "Abastecimento", "Intervenção programada", "Limpeza"]

# --- 2. FUNÇÕES DE SUPORTE E BANCO ---
@st.cache_resource
def get_engine():
    db_url = st.secrets.get("database_url") or os.environ.get("database_url")
    if not db_url:
        st.error("Erro crítico: Configuração do banco de dados não encontrada.")
        st.stop()
    return create_engine(db_url.replace("postgres://", "postgresql://", 1), pool_pre_ping=True)

# --- FUNÇÕES OTIMIZADAS COM CACHE DE CURTA DURAÇÃO PARA NAVEGAÇÃO INSTANTÂNEA ---
@st.cache_data(ttl=30, show_spinner=False)
def carregar_tarefas_empresa(emp_id):
    engine = get_engine()
    return pd.read_sql(text("SELECT * FROM tarefas WHERE empresa_id = :eid ORDER BY data DESC, id DESC"), engine, params={"eid": str(emp_id)})

@st.cache_data(ttl=30, show_spinner=False)
def carregar_planos_master_empresa(emp_id):
    engine = get_engine()
    return pd.read_sql(text("SELECT id, nome_plano, tipo_os, area, prefixo, tipo_criterio, intervalo_valor FROM planos_master WHERE empresa_id = :eid ORDER BY id DESC"), engine, params={"eid": str(emp_id)})

# --- CONFIGURAÇÃO DO MODELO LLAMA 3 (GROQ) & BUSCA WEB ---
def obter_llm():
    api_key = st.secrets.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY")
    return api_key

# --- PESQUISA WEB TÉCNICA ---
def pesquisar_solucao_web(termo_busca: str) -> str:
    """Pesquisa dados técnicos e diagnósticos na internet diretamente via DDGS."""
    try:
        query = f"manutencao automotiva defeito {termo_busca} causa solucao"
        with DDGS() as ddgs:
            resultados = list(ddgs.text(query, max_results=2))
            if resultados:
                trechos = [r.get("body", "") for r in resultados if "body" in r]
                return " ".join(trechos)
        return ""
    except Exception:
        return ""

# --- CHAMADA DIRETA À API GROQ ---
def chamar_groq_direto(prompt_texto, api_key):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": prompt_texto}],
        "temperature": 0.0
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        else:
            return f"Erro da API Groq ({response.status_code}): {response.text}"
    except Exception as e:
        return f"Erro de conexão: {str(e)}"

# --- BUSCA DE HISTÓRICO GERAL NA FROTA POR SIMILARIDADE DE SINTOMA ---
def buscar_historico_relevante(sintoma, emp_id, prefixo=None):
    """Busca ordens de serviço em toda a frota que contenham relação com o sintoma relatado."""
    engine = get_engine()
    
    query_geral_frota = text("""
        SELECT data, prefixo, descricao, COALESCE(executor, 'Não informado') as executor, numero_os, realizado 
        FROM tarefas 
        WHERE empresa_id = :eid
        ORDER BY id DESC LIMIT 50
    """)
    
    try:
        historico_formatado = []
        vistos = set()
        
        palavras_chave = [p.lower() for p in sintoma.split() if len(p) > 3]
        
        with engine.connect() as conn:
            resultados = conn.execute(query_geral_frota, {"eid": str(emp_id)}).fetchall()
            
            for r in resultados:
                dt = str(r[0]) if r[0] else "Data S/N"
                pref = str(r[1]) if r[1] else "S/P"
                desc = str(r[2]).strip() if r[2] else ""
                execut = str(r[3]) if r[3] else "Não informado"
                num_os = f"OS {r[4]}" if r[4] else "Sem Nº"
                realizado = r[5]
                
                desc_lower = desc.lower()
                relevante = (prefixo and str(pref).lower() == str(prefixo).lower()) or any(kw in desc_lower for kw in palavras_chave)
                
                if relevante:
                    status_os = "Concluída" if realizado else "Pendente / Sem retorno de execução"
                    linha = f"[Veículo {pref}] Data: {dt} | {num_os} | Status: {status_os} | Descrição/Serviço: {desc} | Mecânico: {execut}"
                    chave = (dt, pref, desc_lower)
                    if chave not in vistos:
                        vistos.add(chave)
                        historico_formatado.append(linha)

        return historico_formatado[:20] if historico_formatado else ["Nenhum histórico correlato na frota."]
    except Exception as e:
        return [f"Erro ao buscar histórico: {str(e)}"]

# --- TRIAGEM DO MR. HALLEY COM ISOLAMENTO ABSOLUTO DO HISTÓRICO ---
def triagem_mr_halley(sintoma, emp_id, prefixo=None, incluir_saudacao=False):
    try:
        api_key = obter_llm()
        if not api_key:
            return "Erro: Chave da API Groq não configurada."

        historicos = buscar_historico_relevante(sintoma, emp_id, prefixo=prefixo)
        historico_formatado = "\n".join(historicos) if historicos else "Nenhum registro anterior na frota."

        prompt_decisao_e_resposta = f"""
Você é o Mr. Halley, assistente técnico de manutenção da plataforma Up 2 Today.
Veículo: {prefixo if prefixo else "Não informado"}
Sintoma Relatado: "{sintoma}"

Histórico Disponível no Banco de Dados da Frota:
{historico_formatado}

INSTRUÇÕES DE ANÁLISE E RESPOSTA:
1. Avalie se o histórico acima possui um registro com o mesmo sentido ou contexto técnico do sintoma relatado.
2. SE HOUVER HISTÓRICO COMPATÍVEL:
   - Inicie OBRIGATORIAMENTE com a frase exata: "Baseado no histórico local da frota, recomenda-se"
   - É ESTRITAMENTE PROIBIDO usar conhecimentos externos da internet, inventar peças, adicionar cilindros, ajustes ou componentes que não estejam explicitamente escritos no texto do histórico do banco. Siga estritamente o que foi feito ou registrado na OS anterior.
3. SE NÃO HOUVER HISTÓRICO COMPATÍVEL:
   - Inicie OBRIGATORIAMENTE com a frase exata: "Não identificamos registros de falhas semelhantes. Mas com base em pesquisas externas, recomenda‑se"
   - Traga o diagnóstico técnico externo de forma limpa.
4. Mantenha a resposta concisa (máximo de 30 palavras).
"""

        resposta = chamar_groq_direto(prompt_texto_modelo := prompt_decisao_e_resposta, api_key)
        return resposta
        
    except Exception as e:
        return f"⚠️ Erro interno na IA: {str(e)}"
        
# --- PROCESSAMENTO INTELIGENTE DE OS (ESTÁVEL E SEGURO) ---
def processar_comando_os(texto_usuario, emp_id):
    """Gerencia a coleta progressiva, cancelando o fluxo se houver pergunta geral."""
    if "rascunho_os" not in st.session_state:
        st.session_state.rascunho_os = None
    if "aguardando_confirmacao_os" not in st.session_state:
        st.session_state.aguardando_confirmacao_os = False

    hoje_str = str(datetime.now().date())
    rascunho = st.session_state.rascunho_os or {}
    texto_baixo = texto_usuario.lower().strip()

    if texto_baixo in ["cancelar", "cancela", "esquece", "não quero mais", "sair"]:
        st.session_state.rascunho_os = None
        st.session_state.aguardando_confirmacao_os = False
        return "❌ Agendamento de Ordem de Serviço cancelado."

    palavras_confirmacao = ["ok", "sim", "tudo certo", "pode agendar", "confirmo", "confirmar", "fechar", "gerar", "certo", "ok."]
    eh_confirmacao = (
        st.session_state.aguardando_confirmacao_os 
        and (texto_baixo in palavras_confirmacao or any(texto_baixo.startswith(p) for p in ["ok", "sim", "confirmo"]))
    )

    if eh_confirmacao:
        try:
            engine = get_engine()
            nova_os = obter_proxima_os(engine, emp_id)

            pref_final = rascunho.get("prefixo", "S/P")
            desc_final = rascunho.get("descricao", "Serviço via chat")
            exec_final = rascunho.get("executor", "Não definido")
            data_final = rascunho.get("data", hoje_str)
            area_final = rascunho.get("area", "Mecânica")
            turno_final = rascunho.get("turno", "Não definido")
            inicio_final = rascunho.get("inicio", "00:00")
            fim_final = rascunho.get("fim", "00:00")

            with engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO tarefas (data, executor, prefixo, inicio_disp, fim_disp, descricao, area, turno, origem, empresa_id, numero_os)
                        VALUES (:dt, :ex, :pr, :ti, :tf, :ds, :ar, :tu, 'Chat Mr. Halley', :eid, :nos)
                    """),
                    {
                        "dt": str(data_final),
                        "ex": str(exec_final),
                        "pr": str(pref_final),
                        "ti": str(inicio_final),
                        "tf": str(fim_final),
                        "ds": str(desc_final),
                        "ar": str(area_final),
                        "tu": str(turno_final),
                        "eid": str(emp_id),
                        "nos": int(nova_os)
                    }
                )
                conn.commit()

            st.session_state.rascunho_os = None
            st.session_state.aguardando_confirmacao_os = False

            return (
                f"✅ **Ordem de Serviço Nº {nova_os} gerada com sucesso!**\n\n"
                f"- **Veículo:** {pref_final}\n"
                f"- **Serviço:** {desc_final}\n"
                f"- **Área:** {area_final}\n"
                f"- **Data:** {data_final}\n"
                f"- **Turno:** {turno_final}\n"
                f"- **Horário:** {inicio_final} às {fim_final}\n"
                f"- **Executor:** {exec_final}\n\n"
                f"*A OS já foi enviada diretamente para a Agenda Principal.*"
            )
        except Exception as e:
            return f"❌ Ocorreu um erro ao salvar a OS no banco: {str(e)}"

    api_key = obter_llm()
    if not api_key:
        return None

    veiculo_contexto = "Não informado"
    relato_contexto = ""
    if "analises_halley" in st.session_state and st.session_state.analises_halley:
        veiculo_contexto = str(st.session_state.analises_halley[-1].get("veiculo", "Não informado"))
        relato_contexto = str(st.session_state.analises_halley[-1].get("relato", ""))

    ultimas_msgs = ""
    if "mensagens_chat_halley" in st.session_state and st.session_state.mensagens_chat_halley:
        mensagens_recentes = st.session_state.mensagens_chat_halley[-6:]
        ultimas_msgs = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in mensagens_recentes])

    template_fluxo = f"""
Você é o assistente Mr. Halley da plataforma Up 2 Today, especialista em agendamento de OS.

Histórico Recente da Conversa no Chat:
{ultimas_msgs if ultimas_msgs else "Nenhuma mensagem anterior."}

Mensagem Atual do Usuário: "{texto_usuario}"
Rascunho Existente de OS: {json.dumps(rascunho, ensure_ascii=False)}
Em Fluxo de OS Ativo? {bool(rascunho or st.session_state.aguardando_confirmacao_os)}

DIRETRIZ CRÍTICA DE INTERRUPÇÃO:
- Se a mensagem atual do usuário for uma **pergunta sobre o sistema, funcionalidades, dúvidas gerais ou qualquer assunto que mude de contexto** (mesmo que haja um rascunho de OS aberto), você DEVE cancelar o fluxo de OS imediatamente retornando `em_fluxo_os: false`.
- Se o usuário estiver de fato fornecendo dados para continuar o preenchimento da OS, mantenha `em_fluxo_os: true` e preencha os campos.

CAMPOS DA OS:
- prefixo: Número/placa do veículo.
- descricao: Descrição do problema/serviço.
- executor: Mecânico ou responsável.
- data: Data AAAA-MM-DD.
- area: Mecânica, Elétrica, Borracharia, Chapeamento ou Limpeza.
- turno: Não definido, Dia ou Noite.
- inicio: HH:MM
- fim: HH:MM

Responda EXCLUSIVAMENTE em formato JSON puro:

Se for pergunta geral, dúvida ou interrupção:
{{"em_fluxo_os": false}}

Se for continuação do preenchimento da OS:
{{"em_fluxo_os": true, "prefixo": "...", "descricao": "...", "executor": "...", "data": "...", "area": "...", "turno": "...", "inicio": "...", "fim": "..."}}
"""

    try:
        resultado = chamar_groq_direto(template_fluxo, api_key)
        resultado_limpo = resultado.replace("```json", "").replace("```", "").strip()
        dados = json.loads(resultado_limpo)

        if not dados.get("em_fluxo_os"):
            st.session_state.rascunho_os = None
            st.session_state.aguardando_confirmacao_os = False
            return None

        novo_rascunho = rascunho.copy()
        for k in ["prefixo", "descricao", "executor", "data", "area", "turno", "inicio", "fim"]:
            v = dados.get(k)
            if v and v not in ["...", "None", "null", "Não informado"]:
                novo_rascunho[k] = v

        texto_usuario_lower = texto_usuario.lower()
        pede_mesmo_veiculo = any(termo in texto_usuario_lower for termo in ["mesmo veículo", "mesmo carro", "desse veículo", "desse carro", "dele", "mesmo", "este mesmo veículo"])
        
        if not novo_rascunho.get("prefixo") or str(novo_rascunho.get("prefixo")).lower() in ["desse", "desse veículo", "último"]:
            if pede_mesmo_veiculo and veiculo_contexto != "Não informado":
                novo_rascunho["prefixo"] = veiculo_contexto
            else:
                novo_rascunho["prefixo"] = None
                
        pede_mesmo_problema = any(termo in texto_usuario_lower for termo in ["mesmo problema", "mesmo defeito", "igual", "mesma falha"])
        
        if not novo_rascunho.get("descricao") or str(novo_rascunho.get("descricao")).lower() in ["mesmo problema", "problema"]:
            if pede_mesmo_problema and relato_contexto:
                novo_rascunho["descricao"] = relato_contexto
            else:
                novo_rascunho["descricao"] = None

        if not novo_rascunho.get("turno"):
            novo_rascunho["turno"] = "Não definido"
        if not novo_rascunho.get("inicio"):
            novo_rascunho["inicio"] = "00:00"
        if not novo_rascunho.get("fim"):
            novo_rascunho["fim"] = "00:00"

        st.session_state.rascunho_os = novo_rascunho

        campos_faltantes = []
        if not novo_rascunho.get("prefixo"):
            campos_faltantes.append("Prefixo do Veículo")
        if not novo_rascunho.get("descricao"):
            campos_faltantes.append("Descrição do Serviço")
        if not novo_rascunho.get("executor"):
            campos_faltantes.append("Mecânico Responsável")
        if not novo_rascunho.get("data"):
            campos_faltantes.append("Data de Agendamento")
        if not novo_rascunho.get("area"):
            campos_faltantes.append("Área (Mecânica, Elétrica, Borracharia, Chapeamento ou Limpeza)")

        if campos_faltantes:
            st.session_state.aguardando_confirmacao_os = False
            return (
                f"Para prosseguir com a abertura da OS, por favor informe:\n\n"
                f"- **{', '.join(campos_faltantes)}**\n\n"
                f"*(Horários e Turno são opcionais)*"
            )

        st.session_state.aguardando_confirmacao_os = True
        return (
            f"📋 **Resumo da Ordem de Serviço:**\n\n"
            f"- **Veículo:** {novo_rascunho.get('prefixo')}\n"
            f"- **Serviço:** {novo_rascunho.get('descricao')}\n"
            f"- **Área:** {novo_rascunho.get('area')}\n"
            f"- **Data:** {novo_rascunho.get('data')}\n"
            f"- **Turno:** {novo_rascunho.get('turno')}\n"
            f"- **Horário:** {novo_rascunho.get('inicio')} às {novo_rascunho.get('fim')}\n"
            f"- **Executor:** {novo_rascunho.get('executor')}\n\n"
            f"👉 Digite **Ok** para confirmar ou informe ajustes."
        )
    except Exception:
        return None
        
# --- RESPOSTAS GERAIS, CONSULTAS DE OS E MANUAL DA PLATAFORMA ---
def responder_chat_mr_halley(mensagem_usuario, emp_id):
    texto_baixo = mensagem_usuario.lower().strip()

    agradecimentos = ["obrigado", "muito obrigado", "valeu", "show", "perfeito", "agradeço", "obrigada", "tmj", "grato"]
    if any(texto_baixo.startswith(term) or texto_baixo == term for term in agradecimentos):
        return "Por nada! Qualquer dúvida técnica ou se precisar agendar uma nova OS, estou à disposição. 🛠️"

    saudacoes = ["olá", "ola", "bom dia", "boa tarde", "boa noite", "fala halley", "oi"]
    if texto_baixo in saudacoes:
        return "Olá! Como posso ajudar com as manutenções da frota hoje?"

    resposta_os = processar_comando_os(mensagem_usuario, emp_id)
    if resposta_os:
        return resposta_os

    api_key = obter_llm()
    if not api_key:
        return "Desculpe, a conexão com a IA (GROQ_API_KEY) não está configurada nos Secrets do Streamlit."

    contexto_foco_atual = "Nenhum chamado foi analisado recentemente nesta tela."
    prefixo_foco = None
    if "analises_halley" in st.session_state and st.session_state.analises_halley:
        ultima = st.session_state.analises_halley[-1]
        prefixo_foco = ultima.get("veiculo")
        contexto_foco_atual = (
            f"ÚLTIMA ANÁLISE REALIZADA (FOCO ATUAL):\n"
            f"- Veículo em análise: {ultima['veiculo']}\n"
            f"- Falha/Sintoma relatado: '{ultima['relato']}'\n"
            f"- Diagnóstico emitido: {ultima['parecer']}"
        )

    historicos_banco = buscar_historico_relevante(mensagem_usuario, emp_id, prefixo=prefixo_foco)
    contexto_banco = "REGISTROS E HISTÓRICOS DE MANUTENÇÃO NO BANCO:\n" + (
        "\n".join(historicos_banco) if historicos_banco else "Nenhum registro anterior no banco."
    )

    manual_plataforma = """
FUNCIONALIDADES E PASSO A PASSO DA PLATAFORMA UP 2 TODAY:
1. Dashboard: Visão geral da operação e métricas principais.
2. Agenda Principal: Centro operacional para controle de janelas de box e manutenções.
3. Cadastro Direto: Agendamento direto de preventivas e revisões periódicas pelo gestor.
4. Chamados Oficina: Espaço do administrador para visualizar, avaliar, aprovar e processar os chamados enviados pela ponta.
5. OSs Pendentes (Baixa Técnica): Aba onde o gestor clica na linha da OS para preencher a execução e dar a baixa técnica.
6. OSs Concluídas: Histórico e relatórios exportáveis de serviços finalizados.
7. Perfil Motorista / Abrir Solicitação: Aba onde o motorista preenche o prefixo e a descrição para **abrir novos chamados** de manutenção de forma remota.
8. Chat Mr. Halley: Assistente virtual integrado para triagem de falhas, consulta de histórico e abertura conversacional de Ordens de Serviço (OS).
"""

    template_geral = f"""
Você é o Mr. Halley, assistente técnico de manutenção, telemetria e suporte da plataforma Up 2 Today.

{contexto_foco_atual}

{contexto_banco}

{manual_plataforma}

Pergunta do Usuário: "{mensagem_usuario}"

DIRETRIZES DE RESPOSTA:
1. Responda de forma direta, clara e baseada estritamente no manual da plataforma acima.
2. Se o usuário perguntar onde abrir chamados, explique que os motoristas abrem na aba **Abrir Solicitação** (no perfil de motorista), e o gestor gerencia e aprova esses chamados na aba **Chamados Oficina**.
3. Se o usuário perguntar sobre baixa de OS, indique a aba **OSs Pendentes**.
4. Mantenha um tom profissional e técnico (máximo de 4 frases).
"""

    try:
        resposta = chamar_groq_direto(template_geral, api_key)
        return resposta
    except Exception as e:
        return f"Erro ao processar consulta: {str(e)}"
        
# --- CHAT FLUTUANTE EM CSS/HTML + PYTHON COM SCROLL AUTOMÁTICO PARA O TOPO DA RESPOSTA ---
def renderizar_chat_flutuante(emp_id):
    URL_AVATAR_HALLEY = "https://i.postimg.cc/5tBtrL6C/Whats-App-Image-2026-07-23-at-22-35-53.png"
    
    if "chat_aberto_usuario" not in st.session_state:
        st.session_state.chat_aberto_usuario = False

    if "mensagens_chat_halley" not in st.session_state:
        st.session_state.mensagens_chat_halley = [
            {"role": "assistant", "content": "Olá! Sou o Mr. Halley. Como posso te ajudar com a frota?"}
        ]
        
    qtd_analises = len(st.session_state.get("analises_halley", []))
    label_status = f"💬 Mr. Halley ({qtd_analises})" if qtd_analises > 0 else "💬 Mr. Halley (IA)"

    # CSS restrito exclusivamente à chave do expander do chat flutuante (não afeta o resto do sistema)
    st.markdown("""
        <style>
        div.st-key-chat_flutuante_expander div[data-testid="stExpander"] {
            position: fixed !important;
            bottom: 20px !important;
            right: 20px !important;
            width: 410px !important;
            z-index: 999999 !important;
            background-color: #ffffff !important;
            border: 2px solid #C5A059 !important;
            border-radius: 12px !important;
            box-shadow: 0px 6px 20px rgba(0, 0, 0, 0.25) !important;
        }

        div.st-key-chat_flutuante_expander details[data-testid="stExpander"][open] {
            max-height: 80vh !important;
        }

        div.st-key-chat_flutuante_expander div[data-testid="stExpander"] div[data-testid="stChatMessage"] p {
            font-size: 0.95rem !important;
            line-height: 1.45 !important;
        }

        div.st-key-chat_flutuante_expander div[data-testid="stExpander"] div[data-testid="stChatMessage"] img,
        div.st-key-chat_flutuante_expander div[data-testid="stExpander"] div[data-testid="stChatMessageAvatarCustom"] {
            width: 44px !important;
            height: 44px !important;
            min-width: 44px !important;
            min-height: 44px !important;
            border-radius: 50% !important;
        }
        </style>
    """, unsafe_allow_html=True)

    with st.container(key="chat_flutuante_expander"):
        with st.expander(label_status, expanded=st.session_state.chat_aberto_usuario):
            chat_box = st.container(height=390)
            with chat_box:
                for msg in st.session_state.mensagens_chat_halley:
                    avatar = URL_AVATAR_HALLEY if msg["role"] == "assistant" else "👤"
                    with st.chat_message(msg["role"], avatar=avatar):
                        st.markdown(msg["content"])

            if prompt := st.chat_input("Dúvida técnica ou agendar OS...", key="chat_flutuante_input"):
                st.session_state.chat_aberto_usuario = True
                st.session_state.mensagens_chat_halley.append({"role": "user", "content": prompt})
                with chat_box:
                    with st.chat_message("user", avatar="👤"):
                        st.markdown(prompt)
                    with st.chat_message("assistant", avatar=URL_AVATAR_HALLEY):
                        with st.spinner("Processando..."):
                            resp = responder_chat_mr_halley(prompt, emp_id)
                            st.markdown(resp)
                st.session_state.mensagens_chat_halley.append({"role": "assistant", "content": resp})
                
                components.html("""
                    <script>
                        const doc = window.parent.document;
                        const chatContainers = doc.querySelectorAll('div[data-testid="stVerticalBlock"]');
                        chatContainers.forEach(container => {
                            if (container.scrollTop !== undefined) {
                                container.scrollTop = container.scrollHeight;
                            }
                        });
                    </script>
                """, height=0)
                st.rerun()
            
def gerar_pdf_manual_oficial_pro():
    class PDF(FPDF):
        def header(self):
            self.set_font("Arial", "B", 25)
            self.set_text_color(27, 34, 76) 
            self.cell(10, 10, "U", 0, 0)
            self.set_text_color(49, 173, 100)
            self.cell(20, 10, "2T", 0, 1)
            self.ln(5)

        def footer(self):
            self.set_y(-15)
            self.set_font("Arial", "I", 8)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f"Página {self.page_no()}", 0, 0, 'C')

    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    pdf.add_page()
    pdf.ln(50)
    pdf.set_font("Arial", "B", 35)
    pdf.set_text_color(27, 34, 76)
    pdf.cell(190, 20, "MANUAL MASTER", ln=True, align='C')
    pdf.set_font("Arial", "B", 28)
    pdf.set_text_color(49, 173, 100)
    pdf.cell(190, 15, "UP 2 TODAY", ln=True, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", "I", 14)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(190, 10, "Seu Controle. Nossa Prioridade.", ln=True, align='C')
    
    pdf.add_page()
    pdf.set_font("Arial", "B", 18); pdf.set_text_color(27, 34, 76)
    pdf.cell(190, 15, "SUMÁRIO", ln=True); pdf.ln(10)
    
    itens_sumario = [
        ("1. Introdução e Ganhos Estratégicos", "3"),
        ("2. Fluxo de Trabalho (Workflow)", "4"),
        ("3. Operação da Logística (Janelas)", "5"),
        ("4. Perfis de Acesso (Admin vs Motorista)", "6"),
        ("5. Guia: Chamados Oficina", "7"),
        ("6. Guia: Agenda Principal", "8"),
        ("7. Guia: Cadastro Direto", "9"),
        ("8. Assistente Virtual e Pendências", "10")
    ]
    
    for titulo, pagina in itens_sumario:
        pdf.set_font("Arial", "B", 12); pdf.set_text_color(0)
        largura_titulo = pdf.get_string_width(titulo)
        pdf.cell(largura_titulo + 2, 10, titulo, 0, 0)
        espaco_pontos = 175 - largura_titulo
        pdf.set_font("Arial", "", 12)
        pdf.cell(espaco_pontos, 10, "." * int(espaco_pontos/1.5), 0, 0)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(10, 10, pagina, 0, 1, 'R')

    pdf.add_page()
    pdf.set_font("Arial", "B", 16); pdf.set_text_color(27, 34, 76)
    pdf.cell(190, 10, "1. INTRODUÇÃO E GANHOS ESTRATÉGICOS", ln=True)
    pdf.set_font("Arial", "", 11); pdf.set_text_color(0)
    pdf.multi_cell(190, 7, (
        "O Up 2 Today é uma plataforma de gestão integrada que une a operação de pista (Motoristas), "
        "o planejamento (Logística) e a execução (Oficina). O objetivo central é garantir que nenhum "
        "veículo fique parado além do tempo estritamente necessário.\n\n"
        "GANHOS PARA A EMPRESA:\n"
        "- Redução de até 30% no Lead Time de manutenção.\n"
        "- Eliminação total de papéis e planilhas paralelas.\n"
        "- Histórico digital real por prefixo e placa.\n"
        "- Comunicação instantânea entre motorista e oficina."
    ))

    pdf.add_page()
    pdf.set_font("Arial", "B", 16); pdf.set_text_color(27, 34, 76)
    pdf.cell(190, 10, "2. FLUXO DE TRABALHO (WORKFLOW)", ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.multi_cell(190, 7, (
        "O ciclo de vida de uma manutenção no Up 2 Today segue três etapas fundamentais:\n\n"
        "1. Solicitação: O motorista aponta a falha de forma remota via portal.\n"
        "2. Aprovação: O gestor avalia a gravidade, define o executor e a área responsável.\n"
        "3. Execução: A oficina realiza o serviço dentro da janela programada, garantindo a eficiência."
    ))

    pdf.add_page()
    pdf.set_font("Arial", "B", 16); pdf.set_text_color(27, 34, 76)
    pdf.cell(190, 10, "3. OPERAÇÃO DA LOGÍSTICA (JANELAS)", ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.multi_cell(190, 7, (
        "A logística é a peça-chave para o preenchimento da disponibilidade na Agenda Principal.\n"
        "Os campos 'Início Disp.' e 'Fim Disp.' permitem que a oficina organize o pátio, "
        "sabendo exatamente quando o veículo estará livre para o box, evitando ociosidade da equipe."
    ))

    pdf.add_page()
    pdf.set_font("Arial", "B", 16); pdf.set_text_color(27, 34, 76)
    pdf.cell(190, 10, "4. PERFIS DE ACESSO (ADMIN VS MOTORISTA)", ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.multi_cell(190, 7, (
        "PERFIL ADMINISTRADOR: Possui visão sistêmica. Responsável por triar chamados, gerenciar a "
        "agenda, cadastrar novos usuários e analisar métricas de performance.\n\n"
        "PERFIL MOTORISTA: Interface otimizada para dispositivos móveis. O motorista foca em "
        "abrir chamados e acompanhar se o seu veículo já foi liberado, sem acesso a dados sensíveis."
    ))

    pdf.add_page()
    pdf.set_font("Arial", "B", 16); pdf.set_text_color(27, 34, 76)
    pdf.cell(190, 10, "5. GUIA: CHAMADOS OFICINA", ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.multi_cell(190, 7, (
        "1. Analise a descrição técnica enviada pela ponta.\n"
        "2. Preencha o Executor, a Data Programada e a Área de destino.\n"
        "3. Marque a caixa 'Aprovar?' e confirme o processamento.\n"
        "*Importante: Após aprovado, o serviço é migrado instantaneamente para a Agenda Principal.*"
    ))

    pdf.add_page()
    pdf.set_font("Arial", "B", 16); pdf.set_text_color(27, 34, 76)
    pdf.cell(190, 10, "6. GUIA: AGENDA PRINCIPAL", ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.multi_cell(190, 7, (
        "A Agenda é o centro operacional do dia a dia.\n"
        "- Filtros: Navegue por data, turno e área de atuação.\n"
        "- Edição Dinâmica: Altere dados diretamente na grade de visualização.\n"
        "- Conclusão: O check no campo 'OK' é obrigatório para encerrar o ciclo e gerar o histórico."
    ))

    pdf.add_page()
    pdf.set_font("Arial", "B", 16); pdf.set_text_color(27, 34, 76)
    pdf.cell(190, 10, "7. GUIA: CADASTRO DIRETO", ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.multi_cell(190, 7, (
        "Utilize esta aba para manutenções programadas (revisões e trocas de óleo).\n"
        "Diferente dos chamados, o cadastro aqui gera um serviço direto na agenda. "
        "A lista inferior serve para auditoria e exclusão de registros indevidos."
    ))

    pdf.add_page()
    pdf.set_font("Arial", "B", 16); pdf.set_text_color(27, 34, 76)
    pdf.cell(190, 10, "8. ASSISTENTE VIRTUAL E PENDÊNCIAS", ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.multi_cell(190, 7, (
        "O Assistente monitora a integridade dos prazos. O alarme visual no topo indica que "
        "há pendências de datas passadas. O botão 'Resolver' permite ao gestor dar "
        "baixa imediata ou reagendar tarefas para o presente com um único clique."
    ))

    texto_pdf = pdf.output(dest='S')
    return texto_pdf.encode('latin-1', 'replace')

def obter_proxima_os(engine, emp_id):
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT MAX(numero_os) FROM tarefas WHERE empresa_id = :eid"), {"eid": str(emp_id)}).fetchone()
            maior_os = result[0]
            if maior_os is None:
                return 1001 
            return int(maior_os) + 1
    except Exception:
        return 1001

COR_BRONZE = "#4A3C31"  
COR_OURO = "#C5A059"    
COR_CHAPA = "#F7F5F0"   
COR_TEXTO = "#231F20"   

st.set_page_config(page_title=f"{NOME_SISTEMA} - Painel de Controle", layout="wide", page_icon="⚙️")
puxar_topo_para_cima()

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700;800;900&display=swap');

html, body, [data-testid="stAppViewContainer"], .stApp {{ 
    background-color: {COR_CHAPA} !important; 
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif !important;
}}

header[data-testid="stHeader"] {{
    background: transparent !important;
    visibility: visible !important;
    display: block !important;
    height: 3rem !important;
}}

.main, .main .block-container, div[data-testid="stMainBlockContainer"], div[data-testid="stAppViewBlockContainer"] {{
    padding-top: 0rem !important;
    margin-top: 0rem !important; 
}}

section[data-testid="stSidebar"] {{ 
    background: linear-gradient(180deg, #2A211B 0%, #1D1612 100%) !important; 
    border-right: 1px solid #3D3128 !important;
    overflow: hidden !important;
}}
section[data-testid="stSidebar"] > div:first-child {{
    overflow: hidden !important;
    padding-top: 0rem !important;
    padding-bottom: 0.4rem !important;
    padding-left: 0.6rem !important;
    padding-right: 0.6rem !important;
    display: flex !important;
    flex-direction: column !important;
    height: 100vh !important;
}}
section[data-testid="stSidebar"] * {{
    color: #F0EDE6 !important;
}}

section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {{
    gap: 0 !important;
}}
section[data-testid="stSidebar"] hr {{
    margin: 2px 0 20px 0 !important;
    border-color: rgba(197, 160, 89, 0.25) !important;
}}

.logo-container-circular {{
    margin: 0 auto !important;
    border-radius: 50%;
    overflow: hidden;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}}
.logo-img-crop {{
    width: 100%;
    height: 100%;
    object-fit: cover !important;
    display: block;
    margin: 0 auto;
    border-radius: 50%;
}}

.brand-title-gold, .login-brand-title {{
    font-family: 'Cinzel', serif !important;
    font-weight: 800 !important;
    font-size: 0.92rem !important;
    letter-spacing: 1.2px !important;
    background: linear-gradient(135deg, #E6C875 0%, #C5A059 50%, #9B783E 100%) !important;
    -webkit-background-clip: text !important;
    -webkit-text-fill-color: transparent !important;
    margin: 3px auto 0 auto !important;
    text-align: center !important;
    text-transform: uppercase !important;
    white-space: nowrap !important;
    display: block !important;
    width: 100% !important;
}}

.sidebar-nav-title {{
    margin: 10px 0 20px 0 !important;
    padding: 0 !important;
    font-size: 0.82rem !important;
    line-height: 1.15 !important;
    color: #F0EDE6 !important;
}}

section[data-testid="stSidebar"] button[kind="secondary"],
section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"],
section[data-testid="stSidebar"] div[data-testid="stButton"] button[kind="secondary"] {{
    width: 100% !important;
    min-height: 30px !important;
    height: 30px !important;
    margin: 0 !important;
    padding: 2px 8px !important;
    border-radius: 8px !important;
    font-size: 0.90rem !important;
    line-height: 1 !important;
    justify-content: flex-start !important;
    text-align: left !important;
    box-sizing: border-box !important;
    background: transparent !important;
    border-color: transparent !important;
}}

section[data-testid="stSidebar"] button[kind="secondary"] p,
section[data-testid="stSidebar"] button[kind="secondary"] span,
section[data-testid="stSidebar"] button[kind="secondary"] div,
section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] p,
section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] span,
section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] div {{
    font-size: 0.90rem !important;
    line-height: 1 !important;
    margin: 0 !important;
    padding: 0 !important;
    color: #FFFFFF !important;
    -webkit-text-fill-color: #FFFFFF !important;
    text-align: left !important;
    justify-content: flex-start !important;
}}

section[data-testid="stSidebar"] button[kind="secondary"]:hover,
section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"]:hover,
section[data-testid="stSidebar"] button[kind="primary"]:hover,
section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:hover {{
    background: rgba(0, 0, 0, 0.38) !important;
    border-color: rgba(197, 160, 89, 0.55) !important;
    box-shadow: inset 0 0 0 1px rgba(197, 160, 89, 0.18), 0 2px 8px rgba(0, 0, 0, 0.22) !important;
}}

section[data-testid="stSidebar"] button[kind="primary"],
section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"],
section[data-testid="stSidebar"] div[data-testid="stButton"] button[kind="primary"] {{
    width: 100% !important;
    min-height: 30px !important;
    height: 30px !important;
    margin: 0 !important;
    padding: 2px 8px !important;
    border-radius: 8px !important;
    font-size: 0.90rem !important;
    line-height: 1 !important;
    justify-content: flex-start !important;
    text-align: left !important;
    box-sizing: border-box !important;
    background: rgba(197, 160, 89, 0.32) !important;
    border-color: rgba(197, 160, 89, 0.6) !important;
    font-weight: 700 !important;
}}

section[data-testid="stSidebar"] button[kind="primary"] p,
section[data-testid="stSidebar"] button[kind="primary"] span,
section[data-testid="stSidebar"] button[kind="primary"] div,
section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] p,
section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] span,
section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] div {{
    font-size: 0.90rem !important;
    line-height: 1 !important;
    margin: 0 !important;
    padding: 0 !important;
    color: #FFFFFF !important;
    -webkit-text-fill-color: #FFFFFF !important;
    text-align: left !important;
    justify-content: flex-start !important;
}}

section[data-testid="stSidebar"] button:not([key^="nav_btn_"]) {{
    padding: 0.30rem 0.6rem !important;
    min-height: 30px !important;
    font-size: 0.84rem !important;
    text-align: left !important;
    justify-content: flex-start !important;
}}

button, 
button[kind="primary"], 
button[kind="secondary"], 
[data-testid="stBaseButton-primary"], 
[data-testid="stBaseButton-secondary"] {{
    background-color: {COR_BRONZE} !important;
    border: 1.5px solid {COR_OURO} !important;
    border-radius: 6px !important;
    color: #FFFFFF !important;
}}

button p, button span, button div,
[data-testid="stBaseButton-primary"] p, [data-testid="stBaseButton-primary"] span,
[data-testid="stBaseButton-secondary"] p, [data-testid="stBaseButton-secondary"] span {{
    color: #FFFFFF !important;
    -webkit-text-fill-color: #FFFFFF !important;
}}

div.stHorizontalBlock button[kind="primary"] {{
    background-color: {COR_OURO} !important;
    border: 2px solid {COR_BRONZE} !important;
}}

div.stHorizontalBlock button[kind="primary"] p, 
div.stHorizontalBlock button[kind="primary"] span, 
div.stHorizontalBlock button[kind="primary"] div {{
    color: {COR_TEXTO} !important;
    -webkit-text-fill-color: {COR_TEXTO} !important;
}}

.top-fixed-section {{
    position: -webkit-sticky !important;
    position: sticky !important;
    top: 0px !important;
    z-index: 99999 !important;
    background-color: #F7F5F0 !important;
    padding-top: 0px !important; 
    padding-bottom: 2px !important;
    border-bottom: 1.5px solid #E2D9CB !important;
    box-shadow: 0 4px 16px rgba(35, 31, 32, 0.06) !important;
    margin-left: -1.5rem !important;
    margin-right: -1.5rem !important;
    padding-left: 1.5rem !important;
    padding-right: 1.5rem !important;
    margin-bottom: 10px !important;
    transform: translateY(-115px) !important; 
}}

.top-fixed-section div[data-testid="stTextInput"] {{
    margin-top: -2px !important;
}}
.top-fixed-section div[data-testid="stPopover"] button {{
    padding: 4px 10px !important;
    min-height: 38px !important;
    font-size: 0.85rem !important;
}}

.metric-card {{
    background: #FFFFFF;
    border-radius: 18px;
    padding: 22px;
    border: 1px solid #EFEAE1;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.03);
    display: flex;
    align-items: center;
    justify-content: space-between;
}}
.metric-icon-box {{
    width: 58px;
    height: 58px;
    border-radius: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.9rem;
}}
.metric-value {{
    font-size: 2.2rem;
    font-weight: 800;
    color: #1F1915;
    line-height: 1;
    margin-top: 4px;
}}
.metric-label {{
    font-size: 0.9rem;
    color: #8F847B;
    font-weight: 600;
}}
</style>
""", unsafe_allow_html=True)

def exibir_painel_pagamento_pro(origem):
    with st.container(border=True):
        st.markdown(f"""
            <div style='text-align: center; color: #31333F;'>
                <h2 style='color: {COR_BRONZE};'>💼 Pacote Pro - {NOME_SISTEMA}</h2>
                <p style='font-size: 1.4rem; font-weight: bold; color: {COR_OURO}; margin-bottom: 5px;'>R$ 299,00 / mês</p>
                <p style='font-style: italic; font-size: 0.9rem;'>Gestão completa para frotas que não podem parar.</p>
                <div style='text-align: left; background-color: #f0f2f6; padding: 15px; border-radius: 10px; margin: 15px 0; border: 1px solid #ddd;'>
                    <p>✅ <b>Gestão Master:</b> Agenda e Cadastro de Manutenções ilimitados.</p>
                    <p>✅ <b>Equipe Total:</b> Acessos para motoristas e administradores sem limites.</p>
                    <p>✅ <b>Indicadores Inteligentes:</b> Gráficos de performance e Lead Time real.</p>
                    <p>✅ <b>Relatórios Ilimitados:</b> Exportação profissional em PDF e Excel.</p>
                </div>
                <p>Escaneie o QR Code abaixo no app do seu banco:</p>
            </div>
        """, unsafe_allow_html=True)
        _, col_qr, _ = st.columns([1, 1, 1])
        col_qr.image("https://i.postimg.cc/3Nn86MF0/QRcode.png", use_container_width=True)
        st.markdown("<p style='text-align: center;'><b>Chave Pix (Copie e Cole):</b></p>", unsafe_allow_html=True)
        st.code("3a7713a1-0a98-41b6-86b5-268c70cfe3f8")
        if st.button("❌ Minimizar detalhes", key=f"min_btn_{origem}"):
            st.session_state[f"show_pay_{origem}"] = False
            st.rerun()

def inicializar_banco():
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS tarefas (id SERIAL PRIMARY KEY, data TEXT, executor TEXT, prefixo TEXT, inicio_disp TEXT, fim_disp TEXT, descricao TEXT, area TEXT, tipo_os TEXT DEFAULT 'Corretiva', turno TEXT, realizado BOOLEAN DEFAULT FALSE, id_chamado INTEGER, origem TEXT, empresa_id TEXT)"))
            conn.execute(text("CREATE TABLE IF NOT EXISTS chamados (id SERIAL PRIMARY KEY, motorista TEXT, prefixo TEXT, descricao TEXT, data_solicitacao TEXT, status TEXT DEFAULT 'Pendente', empresa_id TEXT)"))
            conn.execute(text("ALTER TABLE tarefas ADD COLUMN IF NOT EXISTS numero_os INTEGER"))
            conn.execute(text("ALTER TABLE tarefas ADD COLUMN IF NOT EXISTS tipo_os TEXT DEFAULT 'Corretiva'"))
            conn.execute(text("ALTER TABLE tarefas ADD COLUMN IF NOT EXISTS plano_id INTEGER"))
            
            # --- TABELA ANTIGA DE PREVENTIVAS (COMPATIBILIDADE) ---
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS planos_preventivas (
                    id SERIAL PRIMARY KEY,
                    empresa_id TEXT NOT NULL,
                    prefixo TEXT NOT NULL,
                    descricao_servico TEXT NOT NULL,
                    tipo_criterio TEXT NOT NULL,
                    intervalo_valor INTEGER NOT NULL,
                    proxima_data_vencimento DATE,
                    ativo BOOLEAN DEFAULT TRUE
                )
            """))

            # --- NOVAS TABELAS PARA PLANOS MASTER E SERVIÇOS AGRUPADOS ---
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS planos_master (
                    id SERIAL PRIMARY KEY,
                    empresa_id TEXT NOT NULL,
                    nome_plano TEXT NOT NULL,
                    tipo_os TEXT NOT NULL,
                    prefixo TEXT NOT NULL,
                    tipo_criterio TEXT DEFAULT 'Dias',
                    intervalo_valor INTEGER DEFAULT 30
                )
            """))

            # Garante a compatibilidade caso a tabela já exista sem essas colunas
            conn.execute(text("ALTER TABLE planos_master ADD COLUMN IF NOT EXISTS tipo_criterio TEXT DEFAULT 'Dias'"))
            conn.execute(text("ALTER TABLE planos_master ADD COLUMN IF NOT EXISTS intervalo_valor INTEGER DEFAULT 30"))
            conn.execute(text("ALTER TABLE planos_master ADD COLUMN IF NOT EXISTS area TEXT DEFAULT 'Mecânica'"))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS servicos_plano (
                    id SERIAL PRIMARY KEY,
                    plano_id INTEGER NOT NULL,
                    descricao_servico TEXT NOT NULL,
                    retorna_valor BOOLEAN DEFAULT FALSE,
                    min_toleravel NUMERIC,
                    max_toleravel NUMERIC
                )
            """))

            # --- TELA DE MEDIDORES (HORÍMETROS / ODÔMETROS) ---
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS medidores_frota (
                    id SERIAL PRIMARY KEY,
                    empresa_id TEXT NOT NULL,
                    prefixo TEXT NOT NULL,
                    data_leitura DATE NOT NULL,
                    horimetro NUMERIC DEFAULT 0,
                    odometro NUMERIC DEFAULT 0
                )
            """))
            conn.commit()
            
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS empresa (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    senha TEXT NOT NULL,
                    data_cadastro DATE DEFAULT CURRENT_DATE,
                    status_assinatura TEXT DEFAULT 'trial',
                    data_expiracao DATE DEFAULT (CURRENT_DATE + INTERVAL '7 days')
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    login TEXT NOT NULL,
                    senha TEXT NOT NULL,
                    perfil TEXT DEFAULT 'motorista',
                    empresa_id TEXT NOT NULL,
                    UNIQUE(login, empresa_id)
                )
            """))
            try: conn.execute(text("ALTER TABLE tarefas ADD COLUMN IF NOT EXISTS empresa_id TEXT DEFAULT 'U2T_MATRIZ'"))
            except Exception: pass
            try: conn.execute(text("ALTER TABLE chamados ADD COLUMN IF NOT EXISTS empresa_id TEXT DEFAULT 'U2T_MATRIZ'"))
            except Exception: pass
            conn.commit()
    except Exception:
        pass

def obter_medidor_proximo(engine, emp_id, prefixo, data_os):
    try:
        with engine.connect() as conn:
            query = text("""
                SELECT horimetro, odometro, ABS(data_leitura - CAST(:dt AS DATE)) as diff_dias
                FROM medidores_frota 
                WHERE empresa_id = :eid AND prefixo = :pref
                ORDER BY diff_dias ASC, data_leitura DESC
                LIMIT 1
            """)
            res = conn.execute(query, {"eid": str(emp_id), "pref": str(prefixo), "dt": str(data_os)}).fetchone()
            if res:
                return float(res[0] or 0), float(res[1] or 0)
    except Exception:
        pass
    return 0.0, 0.0

def to_excel_native(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Manutencoes')
    return output.getvalue()

@st.cache_data(show_spinner=False)
def gerar_pdf_periodo(df_periodo, data_inicio, data_fim):
    pdf = FPDF()
    pdf.add_page()
    
    pdf.set_font("Arial", "B", 22)
    pdf.set_text_color(74, 60, 49) 
    pdf.cell(6, 10, "U", ln=0)     
    pdf.set_text_color(197, 160, 89) 
    pdf.cell(40, 10, "1Y", ln=0)
    
    pdf.set_font("Arial", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(144, 10, f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}", ln=1, align="R")
    
    pdf.set_font("Arial", "B", 14)
    pdf.set_text_color(74, 60, 49)
    pdf.cell(190, 10, f"RELATORIO DE MANUTENCAO - {NOME_SISTEMA.upper()}", ln=True, align="C")
    
    pdf.set_font("Arial", "", 10)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(190, 8, f"Periodo: {data_inicio.strftime('%d/%m/%Y')} ate {data_fim.strftime('%d/%m/%Y')}", ln=True, align="C")
    pdf.ln(5)
    
    if not df_periodo.empty and 'data' in df_periodo.columns:
        for d_process in sorted(df_periodo['data'].unique(), reverse=True):
            d_formatada = pd.to_datetime(d_process).strftime('%d/%m/%Y')
            pdf.set_font("Arial", "B", 11); pdf.set_fill_color(240, 240, 240)
            pdf.cell(190, 8, f" DATA: {d_formatada}", ln=True, fill=True)
            
            for area in ORDEM_AREAS:
                df_area = df_periodo[(df_periodo['data'] == d_process) & (df_periodo['area'] == area)]
                if not df_area.empty:
                    pdf.set_font("Arial", "B", 9); pdf.set_text_color(197, 160, 89)
                    pdf.cell(190, 7, f" Setor: {area}", ln=True)
                    
                    pdf.set_font("Arial", "B", 8); pdf.set_text_color(50); pdf.set_fill_color(230, 230, 230)
                    pdf.cell(20, 6, "Prefixo", 1, 0, 'C', True)
                    pdf.cell(35, 6, "Executor", 1, 0, 'C', True)
                    pdf.cell(40, 6, "Disponibilidade", 1, 0, 'C', True)
                    pdf.cell(95, 6, "Descricao", 1, 1, 'C', True)
                    
                    pdf.set_font("Arial", "", 7); pdf.set_text_color(0)
                    for _, row in df_area.iterrows():
                        pdf.cell(20, 6, str(row['prefixo']), 1, 0, 'C')
                        pdf.cell(35, 6, str(row['executor'])[:20], 1, 0, 'C')
                        pdf.cell(40, 6, f"{row['inicio_disp']} - {row['fim_disp']}", 1, 0, 'C')
                        pdf.cell(95, 6, str(row['descricao'])[:75], 1, 1, 'L')
                    pdf.ln(2)
                
    return bytes(pdf.output())

if "logado" not in st.session_state:
    st.session_state["logado"] = False

if "perfil" not in st.session_state:
    st.session_state["perfil"] = "motorista"

if "usuario_ativo" not in st.session_state:
    st.session_state["usuario_ativo"] = ""

if not st.session_state["logado"]:
    _, col_login, _ = st.columns([1.2, 1, 1.2])
    with col_login:
        st.markdown(f"""
            <div style='text-align: center; margin-bottom: 5px;'>
                <div class='logo-container-circular' style='width: 90px; height: 90px;'>
                    <img src='{LOGO_URL}' class='logo-img-crop'>
                </div>
                <p class='login-brand-title'>UPDATED YESTERDAY</p>
                <p style='text-align: center; font-style: italic; color: #8F847B; margin: 4px 0 30px 0; font-size: 0.78rem;'>{SLOGAN}</p>
            </div>
        """, unsafe_allow_html=True)
        
        aba = st.radio("Selecione uma opção", ["Acessar", "Criar Conta"], horizontal=True, label_visibility="collapsed")
        
        if aba == "Acessar":
            with st.container(border=True):
                user_input = st.text_input("E-mail ou Usuário", key="u_log").lower().strip()
                pw_input = st.text_input("Senha", type="password", key="p_log").strip()
                
                if st.button(f"Acessar Painel {NOME_SISTEMA}", use_container_width=True, type="primary"):
                    if user_input and pw_input:
                        engine = get_engine()
                        inicializar_banco()
                        
                        if user_input == "bruno":
                            try:
                                with engine.connect() as conn:
                                    check_user = conn.execute(text("SELECT id, senha FROM usuarios WHERE LOWER(login) = 'bruno'")).fetchone()
                                    if check_user:
                                        if not verificar_senha(pw_input, str(check_user[1])):
                                            hash_novo = gerar_hash_senha(pw_input)
                                            conn.execute(text("UPDATE usuarios SET senha = :p, perfil = 'admin', empresa_id = 'U2T_MATRIZ' WHERE LOWER(login) = 'bruno'"), {"p": hash_novo})
                                            conn.commit()
                                    else:
                                        hash_novo = gerar_hash_senha(pw_input)
                                        conn.execute(text("INSERT INTO usuarios (login, senha, perfil, empresa_id) VALUES ('bruno', :p, 'admin', 'U2T_MATRIZ')"), {"p": hash_novo})
                                        conn.commit()
                            except Exception:
                                pass

                        with engine.connect() as conn:
                            empresa = conn.execute(
                                text("""
                                    SELECT id, nome, senha FROM empresa 
                                    WHERE LOWER(TRIM(email)) = LOWER(TRIM(:u)) 
                                       OR LOWER(TRIM(nome)) = LOWER(TRIM(:u))
                                """), 
                                {"u": user_input}
                            ).fetchone()
                        
                        if empresa and verificar_senha(pw_input, str(empresa[2])):
                            if not str(empresa[2]).startswith("pbkdf2_sha256$"):
                                try:
                                    with engine.connect() as conn:
                                        conn.execute(text("UPDATE empresa SET senha = :p WHERE id = :id"), {"p": gerar_hash_senha(pw_input), "id": int(empresa[0])})
                                        conn.commit()
                                except Exception:
                                    pass

                            st.session_state["logado"] = True
                            st.session_state["empresa"] = empresa[1]
                            st.session_state["perfil"] = "admin"
                            st.session_state["usuario_ativo"] = user_input
                            st.success("✅ Login efetuado com sucesso!")
                            st.rerun()
                        
                        else:
                            with engine.connect() as conn:
                                usuario = conn.execute(
                                    text("""
                                        SELECT id, empresa_id, perfil, senha FROM usuarios 
                                        WHERE LOWER(TRIM(login)) = LOWER(TRIM(:u))
                                    """), 
                                    {"u": user_input}
                                ).fetchone()
                                
                            if usuario and verificar_senha(pw_input, str(usuario[3])):
                                if not str(usuario[3]).startswith("pbkdf2_sha256$"):
                                    try:
                                        with engine.connect() as conn:
                                            conn.execute(text("UPDATE usuarios SET senha = :p WHERE id = :id"), {"p": gerar_hash_senha(pw_input), "id": int(usuario[0])})
                                            conn.commit()
                                    except Exception:
                                        pass

                                st.session_state["logado"] = True
                                st.session_state["empresa"] = usuario[1]
                                st.session_state["perfil"] = usuario[2]
                                st.session_state["usuario_ativo"] = user_input
                                st.success("✅ Login efetuado com sucesso!")
                                st.rerun()
                            else:
                                st.error("❌ Usuário ou senha incorretos.")
                    else:
                        st.warning("⚠️ Preencha todos os campos para acessar.")
                        
        elif aba == "Criar Conta":
            with st.container(border=True):
                st.markdown(f"<h4 style='color:{COR_BRONZE}'>🚀 7 Dias Grátis</h4>", unsafe_allow_html=True)
                n_emp = st.text_input("Nome da Empresa").strip()
                n_ema = st.text_input("E-mail Corporativo").lower().strip()
                n_sen = st.text_input("Senha", type="password").strip()
                
                if st.button("Criar minha conta agora", use_container_width=True, type="primary"):
                    if n_emp and n_ema and n_sen:
                        try:
                            engine = get_engine()
                            inicializar_banco()
                            expira = datetime.now().date() + timedelta(days=7)
                            senha_protegida = gerar_hash_senha(n_sen)
                            with engine.connect() as conn:
                                conn.execute(text("INSERT INTO empresa (nome, email, senha, data_expiracao) VALUES (:n, :e, :s, :d)"), 
                                             {"n": n_emp, "e": n_ema, "s": senha_protegida, "d": expira})
                                conn.commit()
                            st.success("✅ Conta criada com proteção PBKDF2! Agora faça login na aba 'Acessar'.")
                        except Exception:
                            st.error("Este e-mail já está cadastrado.")
                    else:
                        st.warning("Preencha todos os campos.")

else:
    engine = get_engine()
    inicializar_banco()
    emp_id = st.session_state["empresa"] 
    usuario_ativo = st.session_state.get("usuario_ativo", "")
    
    if st.session_state["perfil"] == "admin" and usuario_ativo != "bruno":
        with engine.connect() as conn:
            dados_exp = conn.execute(text("SELECT data_expiracao, status_assinatura FROM empresa WHERE nome = :n"), {"n": emp_id}).fetchone()
        if dados_exp and dados_exp[1] == 'trial':
            hoje_dt = datetime.now().date()
            data_exp_dt = pd.to_datetime(dados_exp[0]).date()
            dias_rest = (data_exp_dt - hoje_dt).days
            if 0 <= dias_rest <= 2:
                with st.warning(f"📢 **Atenção:** Seu acesso expira em {dias_rest} dias ({data_exp_dt.strftime('%d/%m/%Y')})."):
                    if st.button("Renove agora a sua assinatura", key="renov_btn_banner", type="primary"):
                        st.session_state["show_pay_banner"] = True
                    if st.session_state.get("show_pay_banner"):
                        exibir_painel_pagamento_pro("banner")
    
    if st.session_state["perfil"] == "motorista":
        opcoes = ["✍  Abrir Solicitação", "📋  Status"]
    else:
        opcoes = [
            "⌂  Dashboard",
            "◰  Agenda Principal",
            "🗎  Cadastro Direto",
            "⚡  Alimentar Horímetros/Odômetros",
            "🗀  Chamados Oficina",
            "🗩  Chat Mr. Halley",
            "⧖  OSs Pendentes",
            "✓  OSs Concluídas",
            "🗠  Indicadores",
            "🕮  Manual do Sistema"
        ]
        
        if usuario_ativo == "bruno":
            opcoes.insert(7, "👥  Minha Equipe")
            opcoes.append("★  Gestão Master")

    if "opcao_selecionada" not in st.session_state or st.session_state.opcao_selecionada not in opcoes:
        st.session_state.opcao_selecionada = opcoes[0]
    
    if "radio_key" not in st.session_state:
        st.session_state.radio_key = 0

    def set_nav(target):
        for op in opcoes:
            if target.split()[-1] in op:
                st.session_state.opcao_selecionada = op
                break
        else:
            st.session_state.opcao_selecionada = target
        st.session_state.radio_key += 1

    if "nav" in st.query_params:
        nav_req = st.query_params.get("nav")
        if nav_req:
            set_nav(nav_req)
            del st.query_params["nav"]
            st.rerun()

    IDIOMAS_DISPONIVEIS = ["Português", "English", "Español"]
    TRADUCOES_INTERFACE = {
        "Português": {
            "Ajuda": "Ajuda", "Idioma": "Idioma", "Notificações": "Notificações",
            "NAVEGAÇÃO": "NAVEGAÇÃO", "Buscar veículo, OS, motorista ou serviço...": "Buscar veículo, OS, motorista ou serviço...",
            "Abrir manual": "Abrir Manual do Sistema", "Abrir chat": "Abrir Chat Mr. Halley",
            "Aplicar idioma": "Aplicar idioma", "OSs atrasadas": "OSs atrasadas",
            "chamados pendentes": "chamados pendentes", "Ver OSs atrasadas": "Ver OSs atrasadas",
            "Avaliar chamados": "Avaliar chamados", "Nenhuma notificação nova.": "Nenhuma notificação nova."
        },
        "English": {
            "Ajuda": "Help", "Idioma": "Language", "Notificações": "Notifications",
            "NAVEGAÇÃO": "NAVIGATION", "Buscar veículo, OS, motorista ou serviço...": "Search vehicle, WO, driver or service...",
            "Abrir manual": "Open System Manual", "Abrir chat": "Open Mr. Halley Chat",
            "Aplicar idioma": "Apply language", "OSs atrasadas": "Overdue work orders",
            "chamados pendentes": "pending driver requests", "Ver OSs atrasadas": "View overdue WOs",
            "Avaliar chamados": "Review requests", "Nenhuma notificação nova.": "No new notifications."
        },
        "Español": {
            "Ajuda": "Ayuda", "Idioma": "Idioma", "Notificações": "Notificaciones",
            "NAVEGACIÓN": "NAVEGACIÓN", "Buscar veículo, OS, motorista ou serviço...": "Buscar vehículo, OT, conductor o servicio...",
            "Abrir manual": "Abrir manual del sistema", "Abrir chat": "Abrir chat del Sr. Halley",
            "Aplicar idioma": "Aplicar idioma", "OSs atrasadas": "OT atrasadas",
            "chamados pendentes": "solicitudes pendientes", "Ver OSs atrasadas": "Ver OT atrasadas",
            "Avaliar chamados": "Evaluar solicitudes", "Nenhuma notificação nova.": "No hay notificaciones nuevas."
        }
    }
    TRADUCOES_NAVEGACAO = {
        "⌂  Dashboard": {"English": "⌂  Dashboard", "Español": "⌂  Panel"},
        "◰  Agenda Principal": {"English": "◰  Main Schedule", "Español": "◰  Agenda Principal"},
        "🗎  Cadastro Direto": {"English": "🗎  Direct Registration", "Español": "🗎  Registro Directo"},
        "🗀  Chamados Oficina": {"English": "🗀  Workshop Requests", "Español": "🗀  Solicitudes de Taller"},
        "🗩  Chat Mr. Halley": {"English": "🗩  Mr. Halley Chat", "Español": "🗩  Chat del Sr. Halley"},
        "⧖  OSs Pendentes": {"English": "⧖  Pending WOs", "Español": "⧖  OT Pendientes"},
        "✓  OSs Concluídas": {"English": "✓  Completed WOs", "Español": "✓  OT Concluidas"},
        "🗠  Indicadores": {"English": "🗠  Indicators", "Español": "🗠  Indicadores"},
        "🕮  Manual do Sistema": {"English": "🕮  System Manual", "Español": "🕮  Manual del Sistema"},
        "👥  Minha Equipe": {"English": "👥  My Team", "Español": "👥  Mi Equipo"},
        "★  Gestão Master": {"English": "★  Master Management", "Español": "★  Gestión Master"},
        "✍  Abrir Solicitação": {"English": "✍  Open Request", "Español": "✍  Abrir Solicitud"},
        "📋  Status": {"English": "📋  Status", "Español": "📋  Estado"}
    }
    if "idioma_atual" not in st.session_state:
        st.session_state.idioma_atual = "Português"

    def tr(chave):
        return TRADUCOES_INTERFACE.get(st.session_state.idioma_atual, {}).get(chave, chave)

    def traduzir_nav(opcao):
        return TRADUCOES_NAVEGACAO.get(opcao, {}).get(st.session_state.idioma_atual, opcao)

    def obter_notificacoes_header():
        qtd_atrasadas = 0
        qtd_chamados = 0
        qtd_preventivas = 0
        try:
            hoje_header = str(datetime.now().date())
            limite_alerta = str(datetime.now().date() + timedelta(days=3))
            with engine.connect() as conn:
                qtd_atrasadas = conn.execute(
                    text("SELECT COUNT(*) FROM tarefas WHERE data < :hoje AND realizado = FALSE AND empresa_id = :eid"),
                    {"hoje": hoje_header, "eid": str(emp_id)}
                ).scalar() or 0
                
                if st.session_state.get("perfil") == "admin":
                    qtd_chamados = conn.execute(
                        text("SELECT COUNT(*) FROM chamados WHERE status = 'Pendente' AND empresa_id = :eid"),
                        {"eid": str(emp_id)}
                    ).scalar() or 0
                    
                    # Preventivas próximas do vencimento (vencem hoje ou nos próximos 3 dias)
                    qtd_preventivas = conn.execute(
                        text("SELECT COUNT(*) FROM planos_preventivas WHERE ativo = TRUE AND proxima_data_vencimento <= :limite AND empresa_id = :eid"),
                        {"limite": limite_alerta, "eid": str(emp_id)}
                    ).scalar() or 0
        except Exception:
            pass
        return int(qtd_atrasadas), int(qtd_chamados), int(qtd_preventivas)

    with st.sidebar:
        st.markdown(f"""
            <div style='text-align: center; margin-top: -1.2rem; padding: 0 0 2px 0;'>
                <div class='logo-container-circular' style='width: 90px; height: 90px;'>
                    <img src='{LOGO_URL}' class='logo-img-crop'>
                </div>
                <p class='brand-title-gold'>UPDATED YESTERDAY</p>
                <p style='color: #A89C91; font-size: 0.72rem; margin: 2px 0 20px 0; text-align: center;'>{SLOGAN}</p>
            </div>
        """, unsafe_allow_html=True)
        st.divider()
        
        st.markdown(f"<div class='sidebar-nav-title'>{tr('NAVEGAÇÃO')}</div>", unsafe_allow_html=True)

        for indice, opcao in enumerate(opcoes):
            st.button(
                traduzir_nav(opcao),
                key=f"nav_btn_{indice}_{st.session_state.radio_key}",
                type="primary" if opcao == st.session_state.opcao_selecionada else "secondary",
                use_container_width=True,
                on_click=set_nav,
                args=(opcao,)
            )
            
            # Se for Cadastro Direto e estiver selecionado, exibe os subitens com recuo e setinha orientadora
            if opcao == "🗎  Cadastro Direto" and st.session_state.opcao_selecionada == "🗎  Cadastro Direto":
                sub_opcoes_sidebar = [
                    ("↳ 📝 Agendamento Direto", 0), 
                    ("↳ 📚 Gerenciar Planos Master", 1), 
                    ("↳ ⚡ Gerar OS em Lote (Planos)", 2)
                ]
                for so_label, so_idx in sub_opcoes_sidebar:
                    is_active_sub = st.session_state.get("sub_aba_idx", 0) == so_idx
                    if st.button(so_label, key=f"sidebar_sub_nav_{so_idx}", use_container_width=True, type="primary" if is_active_sub else "secondary"):
                        st.session_state.opcao_selecionada = "🗎  Cadastro Direto"
                        st.session_state.sub_aba_idx = so_idx
                        st.rerun()
        
        st.markdown("""
            <div style='margin-top: 3px; background: rgba(197, 160, 89, 0.08); border: 1px solid #C5A059; border-radius: 8px; padding: 8px 10px; margin-bottom: 4px;'>
                <p style='margin:0; font-weight:700; color:#C5A059; font-size:0.82rem;'>💬 Chat com Mr. Halley</p>
                <p style='margin:2px 0 0 0; font-size:0.72rem; color:#DDD;'>Estamos online para ajudar!</p>
            </div>
        """, unsafe_allow_html=True)

        st.markdown(f"""
            <div style='margin-top: 20px; padding-top: 4px; border-top: 1px solid rgba(197, 160, 89, 0.2);'>
                <p style='margin: 0 0 25px 0; font-size: 0.8rem; line-height: 1.15;'>
                    🏢 <b>{emp_id}</b> | 👤 <b>{st.session_state['perfil'].capitalize()}</b> ({usuario_ativo})
                </p>
            </div>
        """, unsafe_allow_html=True)
        
        if st.button("Sair da Conta", type="primary", use_container_width=True): 
            st.session_state["logado"] = False
            st.rerun()

    st.markdown("<div class='top-fixed-section'>", unsafe_allow_html=True)
    
    qtd_atrasadas_header, qtd_chamados_header, qtd_preventivas_header = obter_notificacoes_header()
    total_notificacoes_header = qtd_atrasadas_header + qtd_chamados_header + qtd_preventivas_header
    
    c_srch, c_help, c_lang, c_notify = st.columns([0.54, 0.16, 0.14, 0.16])

    with c_srch:
        st.text_input(
            "Buscar...",
            placeholder=f"🔍 {tr('Buscar veículo, OS, motorista ou serviço...')}",
            label_visibility="collapsed"
        )

    with c_help:
        with st.popover(f"❔ {tr('Ajuda')}", use_container_width=True):
            st.markdown(f"### ❔ {tr('Ajuda')}")
            st.caption("Acesse o manual da plataforma ou converse com o Mr. Halley para uma primeira triagem.")
            if st.button(f"📖 {tr('Abrir manual')}", key="header_help_manual", use_container_width=True):
                set_nav("Manual do Sistema")
                st.rerun()
            if st.button(f"💬 {tr('Abrir chat')}", key="header_help_chat", use_container_width=True):
                st.session_state.chat_aberto_usuario = True
                set_nav("Chat Mr. Halley")
                st.rerun()

    with c_lang:
        with st.popover(f"🌐 {tr('Idioma')}", use_container_width=True):
            idioma_selecionado = st.selectbox(
                tr("Idioma"),
                IDIOMAS_DISPONIVEIS,
                index=IDIOMAS_DISPONIVEIS.index(st.session_state.idioma_atual),
                key="header_idioma_selecao"
            )
            if idioma_selecionado != st.session_state.idioma_atual:
                st.session_state.idioma_atual = idioma_selecionado
                st.rerun()
            st.caption(tr("Aplicar idioma"))

    with c_notify:
        rotulo_notificacao = f"🔔 {total_notificacoes_header}" if total_notificacoes_header else "🔔"
        with st.popover(rotulo_notificacao, use_container_width=True):
            st.markdown(f"### 🔔 {tr('Notificações')}")
            if qtd_atrasadas_header:
                st.warning(f"⚠️ {qtd_atrasadas_header} {tr('OSs atrasadas')}.")
                if st.button(f"📅 {tr('Ver OSs atrasadas')}", key="header_overdue", use_container_width=True):
                    set_nav("OSs Pendentes")
                    st.rerun()
            if qtd_chamados_header:
                st.info(f"📥 {qtd_chamados_header} {tr('chamados pendentes')}.")
                if st.button(f"📥 {tr('Avaliar chamados')}", key="header_calls", use_container_width=True):
                    set_nav("Chamados Oficina")
                    st.rerun()
            if qtd_preventivas_header:
                st.warning(f"🔧 {qtd_preventivas_header} planos preventivos próximos ao vencimento.")
                if st.button("📅 Ver Cadastro Direto / Preventivas", key="header_prev", use_container_width=True):
                    set_nav("Cadastro Direto")
                    st.rerun()
            if not total_notificacoes_header:
                st.success(tr("Nenhuma notificação nova."))

    cards_acesso = [
        {"alvo": "Dashboard", "icone": "⌂", "titulo": "Dashboard", "descricao": "Visão geral da operação e dos principais indicadores."},
        {"alvo": "Agenda Principal", "icone": "◰", "titulo": "Agenda Principal", "descricao": "Controle de janelas de box e manutenções programadas."},
        {"alvo": "Cadastro Direto", "icone": "🗎", "titulo": "Cadastro Direto", "descricao": "Agendamento de preventivas e revisões periódicas."},
        {"alvo": "Chamados Oficina", "icone": "🗀", "titulo": "Chamados Oficina", "descricao": "Triagem técnica e diagnósticos com o Mr. Halley."},
        {"alvo": "Chat Mr. Halley", "icone": "🗩", "titulo": "Chat Mr. Halley", "descricao": "Primeira triagem de dúvidas técnicas e abertura de OS."},
        {"alvo": "OSs Pendentes", "icone": "⧖", "titulo": "OSs Pendentes", "descricao": "Acompanhe serviços que ainda aguardam conclusão."},
        {"alvo": "OSs Concluídas", "icone": "✓", "titulo": "OSs Concluídas", "descricao": "Consulte o histórico de serviços finalizados."},
        {"alvo": "Indicadores", "icone": "🗠", "titulo": "Indicadores", "descricao": "Visualize métricas e desempenho da manutenção."},
        {"alvo": "Manual do Sistema", "icone": "🕮", "titulo": "Manual do Sistema", "descricao": "Consulte o guia operacional completo da plataforma."}
    ]
    if usuario_ativo == "bruno":
        cards_acesso.extend([
            {"alvo": "Minha Equipe", "icone": "👥", "titulo": "Minha Equipe", "descricao": "Gerencie usuários, perfis e acessos da empresa."},
            {"alvo": "Gestão Master", "icone": "★", "titulo": "Gestão Master", "descricao": "Acesse os recursos administrativos avançados."}
        ])
    if st.session_state["perfil"] == "motorista":
        cards_acesso = [
            {"alvo": "Abrir Solicitação", "icone": "✍", "titulo": "Abrir Solicitação", "descricao": "Registre uma nova necessidade para a oficina."},
            {"alvo": "Status", "icone": "📋", "titulo": "Status", "descricao": "Acompanhe o andamento das suas solicitações."}
        ]

    st.markdown(
        """
        <div style='display: flex; align-items: center; justify-content: space-between; margin: 2px 0 2px 0;'>
            <span style='color: #2D241E; font-weight: 700; font-size: 0.88rem;'>Acesso Rápido</span>
            <span style='color: #8A7E75; font-size: 0.72rem;'>Hover de 2s para rolar continuamente ou clique no card</span>
        </div>
        """,
        unsafe_allow_html=True
    )

    cards_json = json.dumps(cards_acesso, ensure_ascii=False)

    st.components.v1.html(
        f"""
        <style>
            * {{ box-sizing: border-box; font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif; }}
            body {{ margin: 0; padding: 0; background: transparent; overflow: hidden; }}
            .carousel-wrapper {{
                display: flex;
                align-items: center;
                gap: 8px;
                width: 100%;
                padding: 1px 0;
            }}
            .arrow-btn {{
                background-color: #3B2E25;
                border: 1.2px solid #C5A059;
                color: #FFFFFF;
                width: 32px;
                height: 60px;
                border-radius: 8px;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 1.4rem;
                flex-shrink: 0;
                transition: all 0.2s ease;
                user-select: none;
            }}
            .arrow-btn:hover {{
                background-color: #C5A059;
                color: #231F20;
            }}
            .track-window {{
                overflow: hidden;
                flex-grow: 1;
                width: 100%;
            }}
            .track {{
                display: flex;
                gap: 10px;
                transition: transform 0.45s cubic-bezier(0.25, 1, 0.5, 1);
                will-change: transform;
            }}
            .card {{
                flex: 0 0 calc((100% - 20px) / 3);
                min-width: 0;
                background: #FFFFFF;
                border: 1.2px solid #EDE8DF;
                border-radius: 12px;
                padding: 6px 10px;
                display: flex;
                align-items: center;
                gap: 8px;
                height: 60px;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.02);
                box-sizing: border-box;
                cursor: pointer;
                transition: all 0.2s ease;
            }}
            .card:hover {{
                border: 1.2px solid #C5A059;
                transform: translateY(-1px);
                box-shadow: 0 4px 12px rgba(197, 160, 89, 0.18);
            }}
            .icon-box {{
                width: 34px;
                height: 34px;
                background: #FBF8F3;
                border: 1px solid #EAE3D5;
                border-radius: 8px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 1.2rem;
                color: #8C7355;
                flex-shrink: 0;
            }}
            .card-texts {{
                display: flex;
                flex-direction: column;
                min-width: 0;
                justify-content: center;
            }}
            .card-title {{
                margin: 0;
                font-size: 0.84rem;
                font-weight: 700;
                color: #2D241E;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                line-height: 1.15;
            }}
            .card-sub {{
                margin: 1px 0 0 0;
                font-size: 0.70rem;
                color: #8A7E75;
                line-height: 1.15;
                display: -webkit-box;
                -webkit-line-clamp: 2;
                -webkit-box-orient: vertical;
                overflow: hidden;
            }}
        </style>

        <div class="carousel-wrapper">
            <button class="arrow-btn" id="btnPrev">‹</button>
            <div class="track-window">
                <div class="track" id="track"></div>
            </div>
            <button class="arrow-btn" id="btnNext">›</button>
        </div>

        <script>
            const cardsData = {cards_json};
            const track = document.getElementById('track');
            const btnPrev = document.getElementById('btnPrev');
            const btnNext = document.getElementById('btnNext');
            const visibleCount = 3;
            let currentIndex = 0;
            let intervalTimer = null;

            cardsData.forEach((c) => {{
                const el = document.createElement('div');
                el.className = 'card';
                el.innerHTML = `
                    <div class="icon-box">${{c.icone}}</div>
                    <div class="card-texts">
                        <p class="card-title">${{c.titulo}}</p>
                        <p class="card-sub">${{c.descricao}}</p>
                    </div>
                `;
                el.addEventListener('click', () => {{
                    try {{
                        const parentWindow = window.parent;
                        const sidebarBtns = Array.from(parentWindow.document.querySelectorAll('section[data-testid="stSidebar"] button'));
                        const targetBtn = sidebarBtns.find(b => b.innerText && b.innerText.includes(c.alvo));
                        if (targetBtn) {{
                            targetBtn.click();
                        }} else {{
                            const url = new URL(parentWindow.location.href);
                            url.searchParams.set('nav', c.alvo);
                            parentWindow.location.href = url.href;
                        }}
                    }} catch (e) {{
                        window.parent.location.search = '?nav=' + encodeURIComponent(c.alvo);
                    }}
                }});
                track.appendChild(el);
            }});

            const maxIndex = Math.max(0, cardsData.length - visibleCount);

            function updateTrack() {{
                const cardPercent = 100 / visibleCount;
                const gapPx = (currentIndex * 10) / visibleCount;
                track.style.transform = `translateX(calc(-${{currentIndex * cardPercent}}% - ${{gapPx}}px))`;
            }}

            function stepNext() {{
                if (currentIndex < maxIndex) {{
                    currentIndex++;
                }} else {{
                    currentIndex = 0;
                }}
                updateTrack();
            }}

            function stepPrev() {{
                if (currentIndex > 0) {{
                    currentIndex--;
                }} else {{
                    currentIndex = maxIndex;
                }}
                updateTrack();
            }}

            btnNext.addEventListener('click', stepNext);
            btnPrev.addEventListener('click', stepPrev);

            btnNext.addEventListener('mouseenter', () => {{
                clearInterval(intervalTimer);
                intervalTimer = setInterval(stepNext, 2000);
            }});
            btnNext.addEventListener('mouseleave', () => {{
                clearInterval(intervalTimer);
            }});

            btnPrev.addEventListener('mouseenter', () => {{
                clearInterval(intervalTimer);
                intervalTimer = setInterval(stepPrev, 2000);
            }});
            btnPrev.addEventListener('mouseleave', () => {{
                clearInterval(intervalTimer);
            }});
        </script>
        """,
        height=66
    )
    st.markdown("</div>", unsafe_allow_html=True)

    aba_ativa = st.session_state.opcao_selecionada

    if "Dashboard" in aba_ativa:
        st.markdown("<h4 style='color: #2D241E; font-weight: 700; margin-bottom: 16px;'>Cronograma Geral de Manutenção</h4>", unsafe_allow_html=True)
        
        df_dash_stats = pd.read_sql(text("SELECT data, realizado FROM tarefas WHERE empresa_id = :eid"), engine, params={"eid": str(emp_id)})
        agendados_hoje, concluidos_total, pendentes_total = 0, 0, 0
        
        if not df_dash_stats.empty:
            df_dash_stats['data_dt'] = pd.to_datetime(df_dash_stats['data'], errors='coerce').dt.date
            hoje_dt = datetime.now().date()
            agendados_hoje = len(df_dash_stats[df_dash_stats['data_dt'] == hoje_dt])
            concluidos_total = len(df_dash_stats[df_dash_stats['realizado'] == True])
            pendentes_total = len(df_dash_stats[df_dash_stats['realizado'] == False])

        col_m1, col_m2, col_m3 = st.columns(3)
        with col_m1:
            st.markdown(f"""
                <div class='metric-card'>
                    <div class='metric-icon-box' style='background: #F4E8D1; color: #C5A059;'>📅</div>
                    <div style='text-align: right;'>
                        <span class='metric-label'>Agendados hoje</span>
                        <div class='metric-value'>{agendados_hoje}</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

        with col_m2:
            st.markdown(f"""
                <div class='metric-card'>
                    <div class='metric-icon-box' style='background: #3B2E25; color: #FFFFFF;'>✓</div>
                    <div style='text-align: right;'>
                        <span class='metric-label'>Concluídos</span>
                        <div class='metric-value'>{concluidos_total}</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

        with col_m3:
            st.markdown(f"""
                <div class='metric-card'>
                    <div class='metric-icon-box' style='background: #FAECE4; color: #E65100;'>🕒</div>
                    <div style='text-align: right;'>
                        <span class='metric-label'>Pendentes</span>
                        <div class='metric-value'>{pendentes_total}</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        col_filtro, col_exp = st.columns([0.55, 0.45])
        
        with col_filtro:
            with st.container(border=True):
                st.markdown("<h5 style='color: #2D241E;'>🔍 Filtro Operacional</h5>", unsafe_allow_html=True)
                p_sel_dash = st.date_input("Período", [datetime.now().date(), datetime.now().date() + timedelta(days=1)], key="dash_dt_filter")
                f_area_dash = st.selectbox("Área", ["Todas"] + ORDEM_AREAS, key="dash_f_area")
                f_turno_dash = st.selectbox("Turno", ["Todos"] + LISTA_TURNOS, key="dash_f_turno")

        with col_exp:
            with st.container(border=True):
                st.markdown("<h5 style='color: #2D241E;'>📤 Exportações Rápidas</h5>", unsafe_allow_html=True)
                c_btn_pdf, c_btn_xls = st.columns(2)
                with c_btn_pdf:
                    st.download_button("📄 PDF", gerar_pdf_periodo(pd.DataFrame(), datetime.now().date(), datetime.now().date()), "Relatorio.pdf", use_container_width=True, key="dash_pdf_btn")
                with c_btn_xls:
                    st.download_button("📊 EXCEL", to_excel_native(pd.DataFrame()), "Relatorio.xlsx", use_container_width=True, key="dash_xls_btn")

            with st.expander("💡 Como usar a Agenda?", expanded=False):
                st.write("""
                1. Selecione a Ordem de Serviço desejada na lista.
                2. Preencha os horários de início e fim da janela logística.
                3. Finalize a execução na aba de baixa técnica para atualizar os relatórios em tempo real.
                """)

        # --- PAINEL DE MONITORAMENTO DE VENCIMENTOS DE PLANOS NO DASHBOARD ---
        st.divider()
        st.markdown("<h4 style='color: #2D241E; font-weight: 700; margin-bottom: 12px;'>⏳ Controle de Vencimentos de Planos por Veículo</h4>", unsafe_allow_html=True)
        st.caption("Acompanhamento preditivo e preventivo do saldo restante, exibindo a próxima meta de preventiva. Ordenado por urgência.")

        try:
            df_planos_dash = pd.read_sql(text("SELECT id, nome_plano, tipo_os, area, prefixo, tipo_criterio, intervalo_valor FROM planos_master WHERE empresa_id = :eid"), engine, params={"eid": str(emp_id)})

            if not df_planos_dash.empty:
                lista_status_frota = []
                avisos_pendencia_medidor = set()
                
                with engine.connect() as conn:
                    for _, p in df_planos_dash.iterrows():
                        prefs = [x.strip() for x in str(p['prefixo']).split(",") if x.strip()]
                        crit = p['tipo_criterio']
                        intervalo_limite = float(p['intervalo_valor'])
                        
                        for pref in prefs:
                            # 1. Tenta buscar a leitura mais próxima na tabela medidores_frota
                            med = conn.execute(
                                text("SELECT data_leitura, horimetro, odometro FROM medidores_frota WHERE empresa_id = :eid AND prefixo = :pref ORDER BY data_leitura DESC LIMIT 1"),
                                {"eid": str(emp_id), "pref": pref}
                            ).fetchone()
                            
                            med_hor_reg = float(med[1] or 0) if med else 0.0
                            med_odo_reg = float(med[2] or 0) if med else 0.0
                            data_med_reg = str(med[0]) if med and med[0] else "-"

                            # 2. Última OS concluída (para puxar os dados específicos da última preventiva)
                            os_recente = conn.execute(
                                text("""
                                    SELECT data, descricao FROM tarefas 
                                    WHERE empresa_id = :eid AND prefixo = :pref AND realizado = TRUE 
                                    ORDER BY data DESC LIMIT 1
                                """),
                                {"eid": str(emp_id), "pref": pref}
                            ).fetchone()
                            
                            os_hor_reg = 0.0
                            os_odo_reg = 0.0
                            data_os_reg = "-"
                            
                            if os_recente:
                                data_os_reg = str(os_recente[0])
                                desc_os = str(os_recente[1])
                                try:
                                    if "Horímetro:" in desc_os:
                                        h_str = desc_os.split("Horímetro:")[1].split("h")[0].strip()
                                        os_hor_reg = float(h_str)
                                    if "Odômetro:" in desc_os:
                                        o_str = desc_os.split("Odômetro:")[1].split("km")[0].strip()
                                        os_odo_reg = float(o_str)
                                except Exception:
                                    pass

                            # Validação de existência de dados para critérios baseados em medidor
                            tem_leitura_sistema = False
                            if crit == "Horímetro":
                                if med_hor_reg > 0 or os_hor_reg > 0:
                                    tem_leitura_sistema = True
                            elif crit == "Odômetro":
                                if med_odo_reg > 0 or os_odo_reg > 0:
                                    tem_leitura_sistema = True
                            else:
                                tem_leitura_sistema = True # Para critérios em Dias

                            if not tem_leitura_sistema and crit in ["Horímetro", "Odômetro"]:
                                avisos_pendencia_medidor.add(pref)

                            # 3. Determina a "Última Leitura Geral"
                            if crit == "Horímetro":
                                if med_hor_reg >= os_hor_reg:
                                    ultima_leitura_geral = med_hor_reg
                                    data_leitura_geral = data_med_reg
                                else:
                                    ultima_leitura_geral = os_hor_reg
                                    data_leitura_geral = data_os_reg
                            elif crit == "Odômetro":
                                if med_odo_reg >= os_odo_reg:
                                    ultima_leitura_geral = med_odo_reg
                                    data_leitura_geral = data_med_reg
                                else:
                                    ultima_leitura_geral = os_odo_reg
                                    data_leitura_geral = data_os_reg
                            else:
                                ultima_leitura_geral = 0.0
                                data_leitura_geral = data_med_reg if data_med_reg != "-" else data_os_reg

                            # 4. Dados específicos da Última Preventiva
                            if crit == "Horímetro":
                                ultima_preventiva_val = os_hor_reg
                            elif crit == "Odômetro":
                                ultima_preventiva_val = os_odo_reg
                            else:
                                ultima_preventiva_val = 0.0

                            # 5. Cálculo correto do saldo restante e da Próxima Preventiva (Meta)
                            atual_val = ultima_leitura_geral
                            if crit in ["Horímetro", "Odômetro"]:
                                if not tem_leitura_sistema:
                                    saldo_restante = 0.0
                                    proxima_preventiva_val = 0.0
                                elif ultima_preventiva_val > 0 and atual_val >= ultima_preventiva_val:
                                    rodado_desde_ultima = atual_val - ultima_preventiva_val
                                    saldo_restante = intervalo_limite - (rodado_desde_ultima % intervalo_limite)
                                    if saldo_restante <= 0:
                                        saldo_restante = intervalo_limite
                                    blocos = int(rodado_desde_ultima // intervalo_limite) + 1
                                    proxima_preventiva_val = ultima_preventiva_val + (blocos * intervalo_limite)
                                else:
                                    saldo_restante = intervalo_limite - (atual_val % intervalo_limite)
                                    if saldo_restante == 0:
                                        saldo_restante = intervalo_limite
                                    proxima_preventiva_val = atual_val + saldo_restante
                            else:
                                saldo_restante = intervalo_limite
                                proxima_preventiva_val = 0.0
                            
                            lista_status_frota.append({
                                "Plano": p['nome_plano'],
                                "Tipo": p['tipo_os'],
                                "Veículo": pref,
                                "Critério": crit,
                                "Intervalo Padrão": intervalo_limite,
                                "Última Leitura": f"{ultima_leitura_geral:,.1f}".replace(",", ".") if (crit != "Dias" and ultima_leitura_geral > 0) else "⚠️ Sem Leitura",
                                "Data Ref.": data_leitura_geral if ultima_leitura_geral > 0 else "-",
                                "Última Preventiva (Leitura)": f"{ultima_preventiva_val:,.1f}".replace(",", ".") if (crit != "Dias" and ultima_preventiva_val > 0) else "-",
                                "Data da Preventiva": data_os_reg if ultima_preventiva_val > 0 else "-",
                                "Próxima Preventiva": f"{proxima_preventiva_val:,.1f} {'km' if crit=='Odômetro' else 'h'}".replace(",", ".") if (crit != "Dias" and proxima_preventiva_val > 0) else "-",
                                "_saldo_ordem": saldo_restante,
                                "Saldo Restante Estimado": f"{saldo_restante:,.1f} {'km' if crit=='Odômetro' else 'h' if crit=='Horímetro' else 'dias'}".replace(",", ".") if (crit == "Dias" or tem_leitura_sistema) else "Aguardando Leitura"
                            })

                # Exibe aviso customizado orientado por diretrizes de telemetria
                if avisos_pendencia_medidor:
                    veiculos_str = ", ".join(sorted(avisos_pendencia_medidor))
                    st.warning(
                        f"💡 **Orientação de Medidor:** O(s) veículo(s) **{veiculos_str}** não possuem valores de horímetro/odômetro registrados. "
                        f"Caso não tenha informado os valores durante a baixa da preventiva, certifique-se de que a leitura digitada na aba "
                        f"**⚡ Alimentar Horímetros/Odômetros** seja o mais próxima possível da data de realização da preventiva. "
                        f"O sistema utiliza os registros mais próximos como ponto de partida para os cálculos, e a alimentação contínua evita atrasos e distorções no saldo."
                    )

                df_status_final = pd.DataFrame(lista_status_frota)
                if not df_status_final.empty:
                    df_status_final = df_status_final.sort_values(by="_saldo_ordem", ascending=True).drop(columns=["_saldo_ordem"])
                    st.dataframe(df_status_final, use_container_width=True, hide_index=True)
                else:
                    st.info("Nenhum veículo vinculado aos planos cadastrados.")
            else:
                st.info("Nenhum plano master cadastrado para monitoramento.")
        except Exception as e:
            st.info("Cadastre leituras de medidores e planos master para ativar o painel preditivo de vencimentos.")

    elif "Gestão Master" in aba_ativa and usuario_ativo == "bruno":
        st.subheader("👑 Painel de Controle Master")
        
        llm = obter_llm()
        if llm:
            if st.button("✨ Sugerir Manutenção com IA"):
                try:
                    resp = llm.invoke("O motorista relatou barulho na suspensão do veículo X. O que pode ser em poucas palavras?")
                    st.write(resp.content)
                except Exception:
                    st.error("Erro na comunicação com a IA.")
        
        st.info("💡 Bruno, aqui você ativa os pagamentos e define os prazos das empresas.")
        df_empresas = pd.read_sql(text("SELECT id, nome, email, data_cadastro, data_expiracao, status_assinatura FROM empresa ORDER BY id DESC"), engine)
        if not df_empresas.empty:
            for _, row in df_empresas.iterrows():
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([2, 2, 1.5, 1])
                    c1.write(f"**Empresa:** {row['nome']}\n\n**Email:** {row['email']}")
                    c2.write(f"📅 Cadastro: {row['data_cadastro']}\n\n⌛ Expira: {row['data_expiracao']}")
                    status_cor = "green" if row['status_assinatura'] == 'ativo' else "orange"
                    c3.markdown(f"Status: :{status_cor}[{row['status_assinatura'].upper()}]")
                    if row['status_assinatura'] != 'ativo':
                        if c4.button("✅ Ativar", key=f"ativar_{row['id']}", use_container_width=True):
                            with engine.connect() as conn:
                                conn.execute(text("UPDATE empresa SET status_assinatura = 'ativo', data_expiracao = :d WHERE id = :i"), {"d": datetime.now().date() + timedelta(days=365), "i": row['id']})
                                conn.commit()
                            st.rerun()
                    else:
                        if c4.button("🚫 Bloquear", key=f"bloq_{row['id']}", use_container_width=True):
                            with engine.connect() as conn:
                                conn.execute(text("UPDATE empresa SET status_assinatura = 'ativo', data_expiracao = :d WHERE id = :i"), 
                                             {"d": datetime.now().date() + timedelta(days=30), "i": row['id']})
                                conn.commit()
                            st.rerun()

    elif "Abrir Solicitação" in aba_ativa:
        st.subheader("✍️ Nova Solicitação de Manutenção")
        st.info("💡 **Dica:** Informe o prefixo e detalhe o problema para que a oficina possa se programar.")
        
        emp_id = st.session_state.get("empresa", "U2T_MATRIZ")
        
        with st.form("f_ch", clear_on_submit=True):
            p = st.text_input("Prefixo do Veículo")
            d = st.text_area("Descrição do Problema")
            
            if st.form_submit_button("Enviar Solicitação"):
                if p and d:
                    nome_motorista = st.session_state.get("usuario_ativo", "Motorista")
                    
                    with engine.connect() as conn:
                        conn.execute(text("""
                            INSERT INTO chamados (motorista, prefixo, descricao, data_solicitacao, status, empresa_id) 
                            VALUES (:m, :p, :d, :dt, 'Pendente', :eid)
                        """), {
                            "m": nome_motorista, "p": p, "d": d, 
                            "dt": str(datetime.now().date()), "eid": str(emp_id)
                        })
                        conn.commit()
                    st.success("✅ Solicitação enviada com sucesso!")

    elif "Status" in aba_ativa:
        st.subheader("📜 Status dos Meus Veículos")
        st.info("Aqui você pode ver se o seu veículo já foi agendado ou concluído pela oficina.")
        df_status = pd.read_sql(text("SELECT prefixo, data_solicitacao as data, status, descricao FROM chamados WHERE empresa_id = :eid ORDER BY id DESC"), engine, params={"eid": str(emp_id)})
        st.dataframe(df_status, use_container_width=True, hide_index=True)
    
    elif "Manual do Sistema" in aba_ativa:
        st.subheader("📖 Manual Oficial e Treinamento")
        with st.container(border=True):
            st.markdown(f"### 📥 Documentação Oficial {NOME_SISTEMA}")
            try:
                pdf_manual_content = gerar_pdf_manual_oficial_pro()
                st.download_button(
                    label="📥 BAIXAR MANUAL PREMIUM (PDF)",
                    data=pdf_manual_content,
                    file_name="Manual_Up2Today_Pro.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary"
                )
            except Exception:
                st.error("Erro ao gerar o arquivo PDF. Verifique a codificação dos textos.")

        st.divider()
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            with st.expander("👑 Perfil ADMINISTRADOR", expanded=True):
                st.write("- Gestão total da Agenda.\n- Cadastro de usuários.\n- Análise de indicadores.")
        with col_m2:
            with st.expander("🚛 Perfil MOTORISTA", expanded=True):
                st.write("- Interface para celular.\n- Abertura de chamados.\n- Acompanhamento de status.")
        st.info("💡 Este manual explica a diferença entre os níveis de acesso e como maximizar os lucros da oficina.")

    elif "OSs Pendentes" in aba_ativa:
        if 'os_em_baixa' not in st.session_state:
            st.session_state.os_em_baixa = None

        if st.session_state.os_em_baixa is not None:
            os_data = st.session_state.os_em_baixa
            os_num = str(os_data['numero_os']).split('.')[0]
            tipo_os_atual = os_data.get('tipo_os', 'Corretiva')
            
            st.button("⬅️ Voltar para a Lista", on_click=lambda: setattr(st.session_state, 'os_em_baixa', None))
            st.subheader(f"⚡ Baixa Técnica [{tipo_os_atual}]: OS {os_num}")
            
            with st.container(border=True):
                st.write(f"🚜 **Veículo:** {os_data['prefixo']}")
                st.write(f"📝 **Serviço Planejado:** {os_data['descricao']}")
                
                with st.form("form_baixa_exclusiva"):
                    servico_realizado = st.text_area("O que foi feito de fato / Observações gerais?")
                    
                    st.markdown("---")
                    st.markdown("#### ⏱️ Dados de Encerramento e Medidores (Para controle de Planos)")
                    
                    col_b1, col_b2, col_b3 = st.columns(3)
                    data_realizacao_baixa = col_b1.date_input("Data de Realização", datetime.now())
                    horimetro_baixa = col_b2.number_input("Horímetro Atual (Opcional)", min_value=0.0, step=1.0, value=0.0)
                    odometro_baixa = col_b3.number_input("Odômetro Atual (Opcional)", min_value=0.0, step=1.0, value=0.0)
                    
                    # --- CAMPOS DINÂMICOS CONFORME O TIPO DE OS ---
                    respostas_tecnicas = ""
                    if tipo_os_atual == "Preditiva":
                        st.markdown("#### 🔍 Medições Preditivas:")
                        val_medido = st.number_input("Valor Medido (Ex: Pressão, Temperatura ou Desgaste)", value=0.0)
                        respostas_tecnicas = f" | [Valor Medido: {val_medido}]"
                    elif tipo_os_atual == "Checklist":
                        st.markdown("#### ✔️ Avaliação de Checklist:")
                        status_check = st.radio("Status do item:", ["Conforme (C)", "Não conforme (NC)"], horizontal=True)
                        respostas_tecnicas = f" | [Status: {status_check}]"

                    executor = st.text_input("Mecânico Responsável")
                    c1, c2 = st.columns(2)
                    h_ini = c1.text_input("Início", "08:00")
                    h_fim = c2.text_input("Fim", "10:00")

                    if st.form_submit_button("💾 Finalizar e Salvar Baixa"):
                        if not servico_realizado:
                            st.error("A descrição do serviço é obrigatória.")
                        else:
                            pref_veiculo = str(os_data['prefixo']).strip()
                            
                            # PRIORIDADE 1 & 2: Tratamento e Gravação dos Medidores
                            h_final_gravacao = horimetro_baixa
                            o_final_gravacao = odometro_baixa
                            
                            with engine.begin() as conn:
                                # Se o usuário não preencheu os medidores na baixa (Prioridade 2), busca na base o mais próximo da data da OS
                                if h_final_gravacao == 0.0 and o_final_gravacao == 0.0:
                                    med_fallback = conn.execute(
                                        text("""
                                            SELECT horimetro, odometro, ABS(data_leitura - CAST(:dt AS DATE)) as diff_dias
                                            FROM medidores_frota 
                                            WHERE empresa_id = :eid AND prefixo = :pref
                                            ORDER BY diff_dias ASC, data_leitura DESC
                                            LIMIT 1
                                        """),
                                        {"eid": str(emp_id), "pref": pref_veiculo, "dt": str(data_realizacao_baixa)}
                                    ).fetchone()
                                    
                                    if med_fallback:
                                        h_final_gravacao = float(med_fallback[0] or 0.0)
                                        o_final_gravacao = float(med_fallback[1] or 0.0)

                                relato = f"Execução: {servico_realizado}{respostas_tecnicas}; Mecânico: {executor}; Horário: {h_ini}-{h_fim} | [Baixa - Horímetro: {h_final_gravacao}h, Odômetro: {o_final_gravacao}km]"
                                
                                query_update = text("""
                                    UPDATE tarefas 
                                    SET realizado = True, 
                                        data = :dt_baixa,
                                        descricao = 'OS: ' || :os || '; Prefixo: ' || :pref || '; ' || COALESCE(descricao, '') || '; ' || :relato
                                    WHERE id = :id_banco 
                                    AND empresa_id = :eid
                                """)
                                conn.execute(query_update, {
                                    "dt_baixa": str(data_realizacao_baixa),
                                    "relato": str(relato), "os": str(os_num),
                                    "pref": pref_veiculo, "id_banco": int(os_data['id']),
                                    "eid": str(emp_id)
                                })
                                
                            st.cache_data.clear()
                            st.session_state.os_em_baixa = None
                            st.success(f"✅ OS {os_num} finalizada e métricas de controle atualizadas com sucesso!")
                            st.rerun()

        else:
            st.subheader("⏳ Ordens de Serviço em Aberto")
            try:
                query = text("SELECT * FROM tarefas WHERE realizado = False AND empresa_id = :eid ORDER BY id DESC")
                df_p = pd.read_sql(query, engine, params={"eid": str(emp_id)})

                if not df_p.empty:
                    df_p['Nº OS'] = df_p['numero_os'].astype(str).str.replace('.0', '', regex=False)
                    st.info("Clique em uma linha para abrir a tela de baixa.")
                    
                    event = st.dataframe(
                        df_p[['Nº OS', 'prefixo', 'descricao', 'id']], 
                        column_config={"id": None, "Nº OS": st.column_config.TextColumn("Nº OS", width="small")},
                        hide_index=True, use_container_width=True,
                        on_select="rerun", selection_mode="single-row"
                    )

                    if event.selection.rows:
                        st.session_state.os_em_baixa = df_p.iloc[event.selection.rows[0]]
                        st.rerun()
                else:
                    st.info("Nenhuma OS pendente.")
            except Exception as e:
                st.error("Erro ao carregar lista."); st.code(str(e))
    
    elif "OSs Concluídas" in aba_ativa:
        st.subheader("✅ Histórico de OSs Concluídas")
        if st.button("🔄 Atualizar Relatório"):
            st.cache_data.clear()
            st.rerun()

        try:
            query_c = text("""
                SELECT 
                    id,
                    REPLACE(CAST(numero_os AS TEXT), '.0', '') as os_formatada,
                    data,
                    prefixo,
                    descricao 
                FROM tarefas 
                WHERE realizado = True 
                AND empresa_id = :eid
                ORDER BY id DESC
            """)
            with engine.connect() as conn:
                df_c = pd.read_sql(query_c, conn, params={"eid": str(emp_id)})
            
            if not df_c.empty:
                df_c['os_formatada'] = df_c['os_formatada'].replace(['None', '', 'nan'], 'S/N')
                df_view = df_c[['os_formatada', 'data', 'prefixo', 'descricao']].copy()
                df_view.columns = ['Nº OS', 'Data', 'Veículo', 'Prontuário de Manutenção']
                
                st.write(f"### 📋 {len(df_view)} Manutenções Registradas")
                st.dataframe(df_view, use_container_width=True)
                csv = df_view.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Baixar Relatório", csv, "historico_up2today.csv", "text/csv")
            else:
                st.info("Nenhuma OS concluída encontrada.")
        except Exception as e:
            st.error("Erro ao carregar histórico."); st.code(str(e))
            
    elif "Agenda Principal" in aba_ativa:
        st.subheader("📅 Cronograma Geral de Manutenções")
        
        try:
            df_stats = pd.read_sql(text("SELECT data, realizado FROM tarefas WHERE empresa_id = :eid"), engine, params={"eid": str(emp_id)})
            if not df_stats.empty:
                df_stats['data'] = pd.to_datetime(df_stats['data']).dt.date
                hoje_dt = datetime.now().date()
                df_hoje = df_stats[df_stats['data'] == hoje_dt]
                
                m1, m2, m3 = st.columns(3)
                with m1: st.metric("Agendados Hoje", len(df_hoje))
                with m2: st.metric("Concluídos", len(df_hoje[df_hoje['realizado'] == True]))
                with m3: st.metric("Pendentes", len(df_hoje[df_hoje['realizado'] == False]))
                st.divider()
        except Exception:
            st.warning("⚠️ O banco de dados está iniciando. Aguarde alguns segundos.")
            st.stop()

        try:
            df_agenda = carregar_tarefas_empresa(emp_id)
            if not df_agenda.empty:
                df_agenda['Nº OS'] = df_agenda['numero_os'].astype(str).replace(['None', 'nan', 'None.0'], '')
                df_agenda['Nº OS'] = df_agenda['Nº OS'].str.replace('.0', '', regex=False)
            else:
                st.info("Agenda vazia.")
        except Exception as e:
            st.error("Erro ao carregar agenda."); st.code(str(e))
            
        with st.popover("💡 Como usar a Agenda?"):
            st.markdown("""
            1. Selecione a OS na lista.
            2. Grave o áudio citando seu Nome, Prefixo e Horários.
            3. Confira a transcrição e clique em Confirmar.
            """)

        if "exibir_bot" not in st.session_state:
            st.session_state.exibir_bot = True

        st.markdown("""
            <style>
                div[data-testid="stPopoverBody"] { width: 850px !important; max-width: 90vw !important; }
                .pulsing-dot {
                    height: 10px; width: 10px; background-color: #ff4b4b;
                    border-radius: 50%; display: inline-block; margin-right: 5px;
                    box-shadow: 0 0 0 0 rgba(255, 75, 75, 1); animation: pulse 1.5s infinite;
                }
                @keyframes pulse {
                    0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(255, 75, 75, 0.8); }
                    70% { transform: scale(1); box-shadow: 0 0 0 10px rgba(255, 75, 75, 0); }
                    100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(255, 75, 75, 0); }
                }
            </style>
        """, unsafe_allow_html=True)

        df_atrasadas = pd.read_sql(text("SELECT * FROM tarefas WHERE data < :hoje AND realizado = False AND empresa_id = :eid"), 
                                   engine, params={"hoje": str(datetime.now().date()), "eid": str(emp_id)})

        if not df_atrasadas.empty:
            if st.session_state.exibir_bot:
                with st.container(border=True):
                    c_txt, c_solve, c_close = st.columns([0.65, 0.25, 0.1])
                    
                    with c_txt:
                        st.markdown(f"""<span class="pulsing-dot"></span> <b style='color: #ff4b4b;'>🔔 ATENÇÃO:</b> Você possui <b>{len(df_atrasadas)}</b> pendências atrasadas.""", unsafe_allow_html=True)
                    
                    with c_solve:
                        with st.popover("⚙️ Resolver", use_container_width=True):
                            st.markdown("### 🛠️ Gestão de Atrasos")
                            
                            container_botao_topo = st.container()

                            c1, c2 = st.columns(2)
                            if c1.button("✅ Concluir Tudo", use_container_width=True, key="mini_all"):
                                with engine.connect() as conn:
                                    conn.execute(text("UPDATE tarefas SET realizado=True WHERE data < :hoje AND realizado=False AND empresa_id=:eid"), {"hoje":str(datetime.now().date()), "eid":str(emp_id)})
                                    conn.commit()
                                st.cache_data.clear()
                                st.rerun()

                            if c2.button("📅 Trazer p/ Hoje", use_container_width=True, key="mini_today"):
                                with engine.connect() as conn:
                                    conn.execute(text("UPDATE tarefas SET data=:hoje WHERE data < :hoje AND realizado=False AND empresa_id=:eid"), {"hoje":str(datetime.now().date()), "eid":str(emp_id)})
                                    conn.commit()
                                st.cache_data.clear()
                                st.rerun()
                            
                            st.divider()
                            st.markdown("🔍 **Ajuste Pontual ou Baixa Rápida:**")
                            
                            df_atrasadas['Nº OS'] = df_atrasadas['numero_os'].astype(str).str.replace('.0', '', regex=False)
                            
                            event_atraso = st.dataframe(
                                df_atrasadas[['Nº OS', 'data', 'prefixo', 'descricao', 'id']],
                                column_config={
                                    "id": None, 
                                    "Nº OS": st.column_config.TextColumn("Nº OS", width="small"),
                                    "data": st.column_config.DateColumn("Data Original"),
                                    "prefixo": "Veículo", 
                                    "descricao": "Serviço"
                                },
                                hide_index=True, use_container_width=True,
                                on_select="rerun", selection_mode="single-row",
                                key="tabela_atrasos_popover"
                            )

                            if event_atraso.selection.rows:
                                idx_atraso = event_atraso.selection.rows[0]
                                os_data_atraso = df_atrasadas.iloc[idx_atraso]
                                os_label = str(os_data_atraso['Nº OS']) if str(os_data_atraso['Nº OS']) != 'nan' else "Sem Nº"
                                
                                with container_botao_topo:
                                    st.warning(f"OS Selecionada: **{os_label}**")
                                    if st.button(f"🚀 Abrir Baixa Técnica da OS {os_label}", type="primary", use_container_width=True, key="btn_baixa_topo"):
                                        st.session_state.os_em_baixa = os_data_atraso
                                        set_nav("OSs Pendentes")
                                        st.rerun()
                                    st.divider()

                    with c_close:
                        if st.button("❌", key="close_assist"):
                            st.session_state.exibir_bot = False
                            st.rerun()
        else:
            if st.button("🔔 Ver Pendências"):
                st.session_state.exibir_bot = True
                st.rerun()

        st.divider()
        st.info("✍️ **Logística:** Clique nas colunas de **Início** ou **Fim** para preencher. **PCM:** Clique em **Área** ou **Executor** para definir. O salvamento é automático.")
        
        df_a = carregar_tarefas_empresa(emp_id)
        hoje_input, amanha = datetime.now().date(), datetime.now().date() + timedelta(days=1)
        
        c_per, c_area, c_turno = st.columns([0.4, 0.3, 0.3])
        with c_per: p_sel = st.date_input("Filtrar Período", [hoje_input, amanha], key="dt_filter")
        
        opcoes_area = ["Todas"] + ORDEM_AREAS
        opcoes_turno = ["Todos"] + LISTA_TURNOS
        
        with c_area: f_area = st.selectbox("Filtrar Área", opcoes_area)
        with c_turno: f_turno = st.selectbox("Filtrar Turno", opcoes_turno)
        
        c_pdf, c_xls, _ = st.columns([0.2, 0.2, 0.6])

        if not df_a.empty and len(p_sel) == 2:
            df_a['data'] = pd.to_datetime(df_a['data']).dt.date
            df_f = df_a[(df_a['data'] >= p_sel[0]) & (df_a['data'] <= p_sel[1])].copy()
            
            if f_area != "Todas": df_f = df_f[df_f['area'] == f_area]
            if f_turno != "Todos": df_f = df_f[df_f['turno'] == f_turno]
            
            ordem_turno_map = {"Não definido": 0, "Dia": 1, "Noite": 2}
            df_f['turno_idx'] = df_f['turno'].map(ordem_turno_map).fillna(0)
            
            with c_pdf: st.download_button("📥 PDF", gerar_pdf_periodo(df_f, p_sel[0], p_sel[1]), f"Relatorio_U2T_{p_sel[0]}.pdf")
            with c_xls: st.download_button("📊 Excel", to_excel_native(df_f), f"Relatorio_U2T_{p_sel[0]}.xlsx")
            
            for d in sorted(df_f['data'].unique(), reverse=True):
                st.markdown(f"#### 🗓️ {d.strftime('%d/%m/%Y')}")
                areas_para_exibir = ORDEM_AREAS if f_area == "Todas" else [f_area]
                for area in areas_para_exibir:
                    df_area_f = df_f[(df_f['data'] == d) & (df_f['area'] == area)].sort_values(by='turno_idx')
                    if not df_area_f.empty:
                        st.markdown(f"<p class='area-header'>📍 {area}</p>", unsafe_allow_html=True)
                        df_editor_base = df_area_f.set_index('id')
                        
                        edited_df = st.data_editor(
                            df_editor_base[['realizado', 'area', 'turno', 'prefixo', 'inicio_disp', 'fim_disp', 'executor', 'descricao', 'id_chamado']], 
                            column_config={
                                "realizado": st.column_config.CheckboxColumn("OK", width="small"),
                                "area": st.column_config.SelectboxColumn("Área", options=ORDEM_AREAS),
                                "turno": st.column_config.SelectboxColumn("Turno", options=LISTA_TURNOS),
                                "inicio_disp": st.column_config.TextColumn("Início (Preencher)"),
                                "fim_disp": st.column_config.TextColumn("Fim (Preencher)"),
                                "executor": st.column_config.TextColumn("Executor"),
                                "id_chamado": None
                            }, 
                            hide_index=False, use_container_width=True, key=f"ed_ted_{d}_{area}"
                        )

                        if not edited_df.equals(df_editor_base[['realizado', 'area', 'turno', 'prefixo', 'inicio_disp', 'fim_disp', 'executor', 'descricao', 'id_chamado']]):
                            with engine.connect() as conn:
                                for row_id, row in edited_df.iterrows():
                                    conn.execute(text("""
                                        UPDATE tarefas SET 
                                        realizado = :r, area = :ar, turno = :t, prefixo = :p, 
                                        inicio_disp = :i, fim_disp = :f, 
                                        executor = :ex, descricao = :ds 
                                        WHERE id = :id AND empresa_id = :eid
                                    """), {
                                        "r": bool(row['realizado']), "ar": str(row['area']), "t": str(row['turno']), 
                                        "p": str(row['prefixo']), "i": str(row['inicio_disp']), 
                                        "f": str(row['fim_disp']), "ex": str(row['executor']), 
                                        "ds": str(row['descricao']), "id": int(row_id),
                                        "eid": str(emp_id)
                                    })
                                    if row['realizado'] and pd.notnull(row['id_chamado']):
                                        try: 
                                            conn.execute(text("UPDATE chamados SET status = 'Concluído' WHERE id = :ic AND empresa_id = :eid"), 
                                                         {"ic": int(row['id_chamado']), "eid": str(emp_id)})
                                        except Exception: 
                                            pass
                                conn.commit()
                            st.toast("Alteração salva com isolamento de segurança!", icon="✅")
                            time_module.sleep(0.5); st.rerun()

    elif "Cadastro Direto" in aba_ativa:
        st.subheader("📝 Agendamento Direto & Planos Master")
        
        if "sub_aba_idx" not in st.session_state:
            st.session_state.sub_aba_idx = 0
            
        abas_nomes = ["📝 Agendamento Direto", "📚 Gerenciar Planos Master", "⚡ Gerar OS em Lote (Planos)"]
        
        cols_abas = st.columns(3)
        for idx_aba, nome_aba in enumerate(abas_nomes):
            ativo = (st.session_state.sub_aba_idx == idx_aba)
            tipo_botao = "primary" if ativo else "secondary"
            if cols_abas[idx_aba].button(nome_aba, key=f"topo_aba_btn_{idx_aba}", use_container_width=True, type=tipo_botao):
                st.session_state.sub_aba_idx = idx_aba
                st.rerun()
            
        sub_aba_escolhida = st.session_state.sub_aba_idx
        st.divider()

        if sub_aba_escolhida == 0:
            with st.popover("💡 Como usar o Cadastro Direto?"):
                st.markdown("""
                    ### 📝 Guia Rápido - Cadastro
                    1. **Uso:** Utilize para preventivas avulsas ou serviços diretos.
                    2. **Formulário:** Preencha os campos e confirme.
                """)
            
            with st.form("f_d", clear_on_submit=True):
                c1, c2, c3, c4 = st.columns(4)
                with c1: d_i = st.date_input("Data", datetime.now())
                with c2: e_i = st.text_input("Executor")
                with c3: p_i = st.text_input("Prefixo")
                with c4: a_i = st.selectbox("Área", ORDEM_AREAS)
                
                c5, c6 = st.columns(2)
                with c5: t_ini = st.text_input("Início (Ex: 08:00)", "00:00")
                with c6: t_fim = st.text_input("Fim (Ex: 10:00)", "00:00")
                
                ds_i = st.text_area("Descrição")
                
                cc1, cc2 = st.columns(2)
                with cc1: t_i = st.selectbox("Turno", LISTA_TURNOS)
                with cc2: tipo_os_i = st.selectbox("Tipo de OS", LISTA_TIPOS_OS)
                
                df_planos_box = pd.read_sql(text("SELECT id, nome_plano FROM planos_master WHERE empresa_id = :eid"), engine, params={"eid": str(emp_id)})
                lista_nomes_planos = ["Nenhum (Avulso)"] + (df_planos_box['nome_plano'].tolist() if not df_planos_box.empty else [])
                plano_escolhido = st.selectbox("Vincular a um Plano Master (Opcional)", lista_nomes_planos)
                
                if st.form_submit_button("Confirmar Agendamento"):
                    nova_os = obter_proxima_os(engine, emp_id)
                    h_prox, o_prox = obter_medidor_proximo(engine, emp_id, p_i, d_i)
                    desc_com_medidor = f"{ds_i} | [Leitura Ref: Horímetro {h_prox}h, Odômetro {o_prox}km]"
                    
                    plano_id_val = None
                    if plano_escolhido != "Nenhum (Avulso)":
                        row_plano = df_planos_box[df_planos_box['nome_plano'] == plano_escolhido]
                        if not row_plano.empty:
                            plano_id_val = int(row_plano.iloc[0]['id'])

                    with engine.connect() as conn:
                        conn.execute(
                            text("INSERT INTO tarefas (data, executor, prefixo, inicio_disp, fim_disp, descricao, area, tipo_os, turno, plano_id, origem, empresa_id, numero_os) VALUES (:dt, :ex, :pr, :ti, :tf, :ds, :ar, :tp, :tu, :pid, 'Direto', :eid, :nos)"), 
                            {
                                "dt": str(d_i), "ex": e_i, "pr": p_i, "ti": t_ini, "tf": t_fim, 
                                "ds": desc_com_medidor, "ar": a_i, "tp": tipo_os_i, "tu": t_i, 
                                "pid": plano_id_val, "eid": str(emp_id), "nos": nova_os
                            }
                        )
                        conn.commit()
                    st.success(f"✅ SERVIÇO AGENDADO! Nº {nova_os} (Horímetro ref: {h_prox}h | Odômetro ref: {o_prox}km)")
                    st.rerun()

            # --- LISTA GERAL DE SERVIÇOS EXCLUSIVA DA ABA DE AGENDAMENTO DIRETO ---
            st.divider()
            st.subheader("📋 Lista geral de serviços")
            df_lista = carregar_tarefas_empresa(emp_id)
            
            if not df_lista.empty:
                df_lista['data'] = pd.to_datetime(df_lista['data']).dt.date
                df_lista['Exc'] = False
                
                ed_l = st.data_editor(df_lista[['Exc', 'data', 'turno', 'executor', 'prefixo', 'inicio_disp', 'fim_disp', 'descricao', 'area', 'id']], hide_index=True, use_container_width=True, key="ed_lista_servicos_direto")
                
                if st.button("🗑️ Excluir Selecionados", key="btn_excluir_servicos_direto"):
                    with engine.connect() as conn:
                        for i in ed_l[ed_l['Exc']==True]['id'].tolist(): 
                            conn.execute(text("DELETE FROM tarefas WHERE id = :id AND empresa_id = :eid"), {"id": int(i), "eid": str(emp_id)})
                        conn.commit()
                    st.warning("🗑️ Itens excluídos.")
                    st.rerun()
                    
                if st.session_state.get("ed_lista_servicos_direto") and st.session_state.ed_lista_servicos_direto.get("edited_rows"):
                    COLUNAS_PERMITIDAS_TAREFAS = {"data", "turno", "executor", "prefixo", "inicio_disp", "fim_disp", "descricao", "area"}
                    with engine.connect() as conn:
                        for idx, changes in st.session_state.ed_lista_servicos_direto["edited_rows"].items():
                            rid = int(df_lista.iloc[int(idx)]['id'])
                            for col, val in changes.items():
                                if col in COLUNAS_PERMITIDAS_TAREFAS: 
                                    conn.execute(text(f"UPDATE tarefas SET {col} = :v WHERE id = :i AND empresa_id = :eid"), {"v": str(val), "i": rid, "eid": str(emp_id)})
                        conn.commit()
                    st.rerun()

        elif sub_aba_escolhida == 1:
            st.markdown("### 📚 Gestão de Planos Master e Serviços")
            st.info("💡 Cadastre o plano, defina a periodicidade, a área e adicione os serviços.")
            
            # --- FORMULÁRIO 1: CRIAR O PLANO MASTER COM ÁREA E ESTADO REATIVO ---
            with st.form("form_novo_plano", clear_on_submit=True):
                st.markdown("#### ➕ Criar Novo Plano Master")
                p_nome = st.text_input("Nome do Plano (Ex: Preventiva Quinzenal)")
                p_tipo = st.selectbox("Tipo de Plano / OS", ["Preventiva", "Preditiva", "Checklist"])
                p_area = st.selectbox("Área do Plano", ORDEM_AREAS, key="select_area_master")
                
                c_p1, c_p2 = st.columns(2)
                p_criterio = c_p1.selectbox("Critério de Periodicidade", ["Dias", "Horímetro", "Odômetro"], key="select_criterio_master")
                
                if p_criterio == "Dias":
                    val_padrao = 30
                    lbl_int = "Valor do Intervalo (Ex: 15 ou 30 dias)"
                elif p_criterio == "Horímetro":
                    val_padrao = 250
                    lbl_int = "Valor do Intervalo (Ex: 250 horas)"
                else:
                    val_padrao = 10000
                    lbl_int = "Valor do Intervalo (Ex: 10.000 km)"
                
                p_intervalo = c_p2.number_input(lbl_int, min_value=1, value=val_padrao, step=1, key="input_val_intervalo")
                p_prefs = st.text_input("Prefixos dos Veículos Vinculados (Separe por vírgula, ex: 101, 102, 103)")
                
                if st.form_submit_button("💾 Salvar Novo Plano Master"):
                    if p_nome and p_prefs:
                        with engine.connect() as conn:
                            conn.execute(
                                text("""
                                    INSERT INTO planos_master (empresa_id, nome_plano, tipo_os, area, prefixo, tipo_criterio, intervalo_valor) 
                                    VALUES (:eid, :nome, :tipo, :area, :pref, :crit, :ival)
                                """),
                                {
                                    "eid": str(emp_id), "nome": p_nome, "tipo": p_tipo, "area": p_area,
                                    "pref": p_prefs.strip(), "crit": p_criterio, "ival": int(p_intervalo)
                                }
                            )
                            conn.commit()
                        st.success("✅ Plano Master criado com sucesso!")
                        st.rerun()
                    else:
                        st.warning("Preencha o nome do plano e informe ao menos um veículo/prefixo.")

            st.divider()

            # --- FORMULÁRIO 2: ADICIONAR SERVIÇOS ---
            st.markdown("#### ➕ Adicionar Serviços a um Plano Existente")
            
            df_m_box = pd.read_sql(text("SELECT id, nome_plano, tipo_os FROM planos_master WHERE empresa_id = :eid"), engine, params={"eid": str(emp_id)})
            
            if not df_m_box.empty:
                mapa_planos = {f"{row['nome_plano']} ({row['tipo_os']})": row['id'] for _, row in df_m_box.iterrows()}
                plano_selecionado_nome = st.selectbox("Selecione o Plano Master para adicionar serviço", list(mapa_planos.keys()))
                id_plano_ativo = mapa_planos[plano_selecionado_nome]
                
                tipo_do_plano_atual = df_m_box[df_m_box['id'] == id_plano_ativo].iloc[0]['tipo_os']

                with st.container(border=True):
                    s_desc = st.text_input("Descrição do Serviço Específico (Ex: Medição de Vibração)", key="input_desc_servico_master")
                    
                    retorna_val = False
                    min_tol, max_tol = 0.0, 0.0
                    
                    if tipo_do_plano_atual == "Preditiva":
                        retorna_val = st.checkbox("Este serviço retorna valor medido?", key="chk_retorna_valor_master")
                        if retorna_val:
                            col_m1, col_m2 = st.columns(2)
                            min_tol = col_m1.number_input("Mínimo Tolerável", value=0.0, key="num_min_toleravel")
                            max_tol = col_m2.number_input("Máximo Tolerável", value=0.0, key="num_max_toleravel")
                    elif tipo_do_plano_atual == "Checklist":
                        st.caption("ℹ️ Este item será avaliado como Conforme (C) ou Não Conforme (NC) na baixa da OS.")

                    if st.button("➕ Adicionar Serviço ao Plano", type="primary", key="btn_add_servico_direto_master"):
                        if s_desc:
                            with engine.connect() as conn:
                                conn.execute(
                                    text("INSERT INTO servicos_plano (plano_id, descricao_servico, retorna_valor, min_toleravel, max_toleravel) VALUES (:pid, :desc, :ret, :minv, :maxv)"),
                                    {"pid": id_plano_ativo, "desc": s_desc, "ret": retorna_val, "minv": min_tol, "maxv": max_tol}
                                )
                                conn.commit()
                            st.success("✅ Serviço adicionado com sucesso ao plano!")
                            st.rerun()
                        else:
                            st.warning("Digite a descrição do serviço.")
            else:
                st.info("Cadastre um Plano Master acima para poder inserir serviços nele.")

            st.divider()
            st.subheader("📋 Planos Cadastrados (Clique para Expandir e Gerenciar)")
            
            # --- LISTAGEM COM ACORDEÃO SEGURO ---
            df_planos_master = carregar_planos_master_empresa(emp_id)
            
            if not df_planos_master.empty:
                with st.container(key="container_lista_planos_master"):
                    for _, plano in df_planos_master.iterrows():
                        pid = plano['id']
                        p_nome = plano['nome_plano']
                        p_tipo = plano['tipo_os']
                        p_area = plano.get('area', 'Mecânica')
                        p_crit = plano['tipo_criterio']
                        p_ival = plano['intervalo_valor']
                        p_veiculos = plano['prefixo']
                        
                        if p_crit == "Odômetro":
                            texto_periodicidade = f"A cada {p_ival:,} km".replace(",", ".")
                        elif p_crit == "Horímetro":
                            texto_periodicidade = f"A cada {p_ival} horas"
                        else:
                            texto_periodicidade = f"A cada {p_ival} dias"

                        with st.expander(f"📦 {p_nome} — [{p_tipo} | {p_area}] | {texto_periodicidade}"):
                            col_info, col_b1, col_b2 = st.columns([0.6, 0.2, 0.2])
                            with col_info:
                                st.markdown(f"**Área:** `{p_area}` | **Veículos Vinculados:** `{p_veiculos}`")
                            with col_b1:
                                btn_edita = st.button("✏️ Editar Plano", key=f"edit_p_{pid}", use_container_width=True)
                            with col_b2:
                                btn_exclui = st.button("🗑️ Excluir Plano", key=f"del_p_{pid}", use_container_width=True)
                                
                            if btn_exclui:
                                with engine.connect() as conn:
                                    conn.execute(text("DELETE FROM servicos_plano WHERE plano_id = :pid"), {"pid": int(pid)})
                                    conn.execute(text("DELETE FROM planos_master WHERE id = :pid"), {"pid": int(pid)})
                                    conn.commit()
                                st.warning("Plano excluído com sucesso!")
                                st.rerun()

                            if btn_edita:
                                st.session_state[f"editando_{pid}"] = True

                            if st.session_state.get(f"editando_{pid}", False):
                                with st.form(f"form_edicao_plano_{pid}"):
                                    novo_nome = st.text_input("Alterar Nome do Plano", value=p_nome)
                                    nova_area = st.selectbox("Área", ORDEM_AREAS, index=ORDEM_AREAS.index(p_area) if p_area in ORDEM_AREAS else 0)
                                    nc1, nc2 = st.columns(2)
                                    novo_crit = nc1.selectbox("Critério", ["Dias", "Horímetro", "Odômetro"], index=["Dias", "Horímetro", "Odômetro"].index(p_crit) if p_crit in ["Dias", "Horímetro", "Odômetro"] else 0)
                                    novo_ival = nc2.number_input("Intervalo", value=int(p_ival), min_value=1)
                                    novo_veiculos = st.text_input("Veículos Vinculados", value=p_veiculos)
                                    
                                    c_salvar, c_cancelar = st.columns(2)
                                    if c_salvar.form_submit_button("💾 Salvar"):
                                        with engine.connect() as conn:
                                            conn.execute(
                                                text("UPDATE planos_master SET nome_plano = :nome, area = :area, tipo_criterio = :crit, intervalo_valor = :ival, prefixo = :pref WHERE id = :pid"),
                                                {"nome": novo_nome, "area": nova_area, "crit": novo_crit, "ival": int(novo_ival), "pref": novo_veiculos, "pid": int(pid)}
                                            )
                                            conn.commit()
                                        st.session_state[f"editando_{pid}"] = False
                                        st.success("✅ Plano atualizado com sucesso!")
                                        st.rerun()
                                    if c_cancelar.form_submit_button("❌ Cancelar"):
                                        st.session_state[f"editando_{pid}"] = False
                                        st.rerun()
                                        
                            st.divider()
                            st.markdown("##### 🛠️ Serviços deste Plano:")
                            
                            df_servicos_vinculados = pd.read_sql(text("SELECT id, descricao_servico, retorna_valor, min_toleravel, max_toleravel FROM servicos_plano WHERE plano_id = :pid"), engine, params={"pid": int(pid)})
                            
                            if not df_servicos_vinculados.empty:
                                for _, serv in df_servicos_vinculados.iterrows():
                                    sid = serv['id']
                                    s_desc = serv['descricao_servico']
                                    s_ret = serv['retorna_valor']
                                    s_min = serv['min_toleravel']
                                    s_max = serv['max_toleravel']
                                    
                                    with st.container(border=True):
                                        cs1, cs2, cs3 = st.columns([0.65, 0.17, 0.18])
                                        
                                        info_resumo = s_desc
                                        if s_ret:
                                            info_resumo += f" *(Retorna Valor | Mín: {s_min} | Máx: {s_max})*"
                                            
                                        cs1.markdown(f"• {info_resumo}")
                                        
                                        edit_serv = cs2.button("✏️ Editar", key=f"edit_serv_{sid}", use_container_width=True)
                                        del_serv = cs3.button("🗑️ Excluir", key=f"del_serv_{sid}", use_container_width=True)
                                        
                                        if del_serv:
                                            with engine.connect() as conn:
                                                conn.execute(text("DELETE FROM servicos_plano WHERE id = :sid"), {"sid": int(sid)})
                                                conn.commit()
                                            st.success("Serviço removido do plano!")
                                            st.rerun()
                                            
                                        if edit_serv:
                                            st.session_state[f"editando_serv_{sid}"] = True
                                            
                                        if st.session_state.get(f"editando_serv_{sid}", False):
                                            with st.form(f"form_edit_serv_{sid}"):
                                                novo_desc_serv = st.text_input("Descrição do Serviço", value=s_desc)
                                                novo_ret_serv = st.checkbox("Retorna valor medido?", value=bool(s_ret))
                                                
                                                novo_min_serv, novo_max_serv = float(s_min or 0), float(s_max or 0)
                                                if novo_ret_serv:
                                                    cm1, cm2 = st.columns(2)
                                                    novo_min_serv = cm1.number_input("Novo Mínimo", value=float(s_min or 0))
                                                    novo_max_serv = cm2.number_input("Novo Máximo", value=float(s_max or 0))
                                                    
                                                bs_salvar, bs_cancel = st.columns(2)
                                                if bs_salvar.form_submit_button("💾 Salvar"):
                                                    with engine.connect() as conn:
                                                        conn.execute(
                                                            text("UPDATE servicos_plano SET descricao_servico = :desc, retorna_valor = :ret, min_toleravel = :minv, max_toleravel = :maxv WHERE id = :sid"),
                                                            {"desc": novo_desc_serv, "ret": novo_ret_serv, "minv": novo_min_serv, "maxv": novo_max_serv, "sid": int(sid)}
                                                        )
                                                        conn.commit()
                                                    st.session_state[f"editando_serv_{sid}"] = False
                                                    st.success("✅ Serviço atualizado!")
                                                    st.rerun()
                                                if bs_cancel.form_submit_button("❌ Cancelar"):
                                                    st.session_state[f"editando_serv_{sid}"] = False
                                                    st.rerun()
                            else:
                                st.info("⚠️ Nenhum serviço foi vinculado a este plano ainda.")
            else:
                st.info("Nenhum plano cadastrado.")

        else:
            st.markdown("### ⚡ Geração de Ordens de Serviço em Lote via Planos Master")
            st.info("💡 Selecione um plano cadastrado, escolha a data de execução, os horários (opcionais) e os veículos para gerar as OSs.")

            df_planos_lote = carregar_planos_master_empresa(emp_id)

            if not df_planos_lote.empty:
                mapa_p_lote = {f"{row['nome_plano']} ({row['tipo_os']} - Cada {row['intervalo_valor']} {row['tipo_criterio'].lower()})": row['id'] for _, row in df_planos_lote.iterrows()}
                plano_lote_escolhido = st.selectbox("Selecione o Plano Master", list(mapa_p_lote.keys()), key="sel_plano_lote")
                id_plano_lote = mapa_p_lote[plano_lote_escolhido]
                
                dados_plano_atual = df_planos_lote[df_planos_lote['id'] == id_plano_lote].iloc[0]
                prefixos_padrao = [p.strip() for p in str(dados_plano_atual['prefixo']).split(",") if p.strip()]

                with st.form("form_geracao_lote_os"):
                    dt_lote = st.date_input("Data de Execução Programada", datetime.now(), key="dt_lote_exec")
                    
                    c_l1, c_l2 = st.columns(2)
                    t_ini_lote = c_l1.text_input("Início (Ex: 08:00 - Deixe vazio se preferir)", "", key="lote_t_ini")
                    t_fim_lote = c_l2.text_input("Fim (Ex: 10:00 - Deixe vazio se preferir)", "", key="lote_t_fim")
                    
                    turno_lote = st.selectbox("Turno", LISTA_TURNOS, key="turno_lote_exec")
                    executor_lote = st.text_input("Executor / Mecânico Padrão", key="exec_lote_exec")
                    
                    veiculos_selecionados = st.multiselect(
                        "Veículos / Equipamentos Alvo para esta OS",
                        options=prefixos_padrao,
                        default=prefixos_padrao,
                        key="multi_veiculos_lote"
                    )
                    
                    if st.form_submit_button("🚀 Gerar OSs para os Veículos Selecionados"):
                        if veiculos_selecionados:
                            df_serv_lote = pd.read_sql(text("SELECT descricao_servico FROM servicos_plano WHERE plano_id = :pid"), engine, params={"pid": int(id_plano_lote)})
                            
                            if not df_serv_lote.empty:
                                descricao_unificada = " | ".join(df_serv_lote['descricao_servico'].tolist())
                                area_plano_lote = dados_plano_atual.get('area', 'Mecânica')
                                
                                with engine.connect() as conn:
                                    contador_gerados = 0
                                    for pref in veiculos_selecionados:
                                        nova_os = obter_proxima_os(engine, emp_id)
                                        h_prox, o_prox = obter_medidor_proximo(engine, emp_id, pref, dt_lote)
                                        desc_final_os = f"[{dados_plano_atual['nome_plano']}] Servicos: {descricao_unificada} | [Leitura Ref: Horímetro {h_prox}h, Odômetro {o_prox}km]"
                                        
                                        conn.execute(
                                            text("""
                                                INSERT INTO tarefas (data, executor, prefixo, inicio_disp, fim_disp, descricao, area, tipo_os, turno, plano_id, origem, empresa_id, numero_os) 
                                                VALUES (:dt, :ex, :pr, :ti, :tf, :ds, :ar, :tp, :tu, :pid, 'Plano Master', :eid, :nos)
                                            """), 
                                            {
                                                "dt": str(dt_lote), "ex": executor_lote, "pr": pref, "ti": t_ini_lote, "tf": t_fim_lote,
                                                "ds": desc_final_os, "ar": area_plano_lote, "tp": dados_plano_atual['tipo_os'], "tu": turno_lote, 
                                                "pid": int(id_plano_lote), "eid": str(emp_id), "nos": nova_os
                                            }
                                        )
                                        contador_gerados += 1
                                    conn.commit()
                                st.success(f"✅ {contador_gerados} Ordens de Serviço geradas e enviadas para a Agenda Principal com sucesso!")
                                st.rerun()
                            else:
                                st.warning("⚠️ Este plano master não possui serviços cadastrados para gerar a OS.")
                        else:
                            st.warning("Selecione pelo menos um veículo.")
            else:
                st.info("Nenhum plano master cadastrado para geração em lote.")
                
    elif "Alimentar Horímetros" in aba_ativa:
        st.subheader("⚡ Alimentação de Horímetros e Odômetros da Frota")
        st.info("💡 Alimente regularmente as leituras para que o sistema cruze com as datas de realização das OSs. Você também pode extrair a lista em Excel abaixo.")
        
        with st.form("form_medidor", clear_on_submit=True):
            mc1, mc2, mc3 = st.columns(3)
            m_pref = mc1.text_input("Prefixo do Veículo/Equipamento")
            m_data = mc2.date_input("Data da Leitura", datetime.now())
            m_hor = mc3.number_input("Horímetro (h)", min_value=0.0, step=1.0)
            m_odo = st.number_input("Odômetro (km)", min_value=0.0, step=1.0)
            
            if st.form_submit_button("💾 Salvar Leitura"):
                if m_pref:
                    with engine.connect() as conn:
                        conn.execute(
                            text("INSERT INTO medidores_frota (empresa_id, prefixo, data_leitura, horimetro, odometro) VALUES (:eid, :pref, :dt, :hor, :odo)"),
                            {"eid": str(emp_id), "pref": m_pref, "dt": str(m_data), "hor": m_hor, "odo": m_odo}
                        )
                        conn.commit()
                    st.success("✅ Leitura de medidor salva com sucesso!")
                    st.rerun()
                else:
                    st.warning("Informe o prefixo do veículo.")

        st.divider()
        st.subheader("📋 Lista de Leituras Acumuladas")
        
        df_med = pd.read_sql(text("SELECT id, prefixo, data_leitura, horimetro, odometro FROM medidores_frota WHERE empresa_id = :eid ORDER BY data_leitura DESC"), engine, params={"eid": str(emp_id)})
        if not df_med.empty:
            st.dataframe(df_med, use_container_width=True, hide_index=True)
            
            # Botão de Exportação para Excel
            excel_bytes = to_excel_native(df_med)
            st.download_button(
                label="📊 Exportar Leituras para Excel",
                data=excel_bytes,
                file_name="medidores_frota.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        else:
            st.info("Nenhuma leitura de medidor registrada até o momento.")

    elif "Chamados Oficina" in aba_ativa:
        c_tit, c_refresh = st.columns([0.8, 0.2])
        with c_tit: 
            st.subheader("📥 Aprovação de Chamados")
            
        with c_refresh:
            if st.button("🔄 Atualizar Lista", use_container_width=True, key="btn_refresh_chamados"):
                if 'df_ap_work' in st.session_state: 
                    del st.session_state.df_ap_work
                if 'analises_halley' in st.session_state:
                    del st.session_state.analises_halley
                st.rerun()

        with st.popover("💡 Como usar os Chamados?"):
            st.markdown("""
                ### 📥 Guia Rápido - Chamados
                1. **Triagem:** Veja o que os motoristas relataram. 
                2. **Aprovação:** Marque a caixa **Aprovar?** para o Mr. Halley dar o diagnóstico de cada veículo!
                3. **Planejamento:** Defina o Executor, o Tipo de OS e a Área com base nos pareceres.
                4. **Finalizar:** Clique em **Processar Agendamentos**.
            """)
            
        df_p = pd.read_sql(text("SELECT id, data_solicitacao, motorista, prefixo, descricao FROM chamados WHERE status = 'Pendente' AND empresa_id = :eid ORDER BY id DESC"), engine, params={"eid": str(emp_id)})
        
        if not df_p.empty:
            if 'df_ap_work' not in st.session_state:
                df_p['Aprovar'] = False
                df_p['Tipo_OS'] = "Corretiva"
                df_p['Executor'] = ""
                df_p['Area_Destino'] = "Mecânica"
                df_p['Data_Programada'] = datetime.now().date()
                df_p['Inicio'] = "00:00"
                df_p['Fim'] = "00:00"
                
                colunas_ordenadas = ['Aprovar', 'prefixo', 'descricao', 'motorista', 'Tipo_OS', 'Area_Destino', 'Executor', 'Data_Programada', 'Inicio', 'Fim', 'data_solicitacao', 'id']
                st.session_state.df_ap_work = df_p[colunas_ordenadas]
            
            if "editor_chamados" in st.session_state and st.session_state.editor_chamados.get("edited_rows"):
                alteracoes = st.session_state.editor_chamados["edited_rows"]
                
                if "analises_halley" not in st.session_state or not isinstance(st.session_state.analises_halley, list):
                    st.session_state.analises_halley = []

                for c_idx_str, campos in alteracoes.items():
                    c_idx = int(c_idx_str)
                    if c_idx < len(st.session_state.df_ap_work):
                        dados_linha = st.session_state.df_ap_work.iloc[c_idx]
                        id_chamado = dados_linha['id']
                        
                        if campos.get("Aprovar") is True:
                            ja_analisado = any(a["id"] == id_chamado for a in st.session_state.analises_halley)
                            
                            if not ja_analisado:
                                with st.spinner(f"🤖 Mr. Halley analisando Veículo {dados_linha['prefixo']}..."):
                                    diag = triagem_mr_halley(
                                        sintoma=dados_linha['descricao'], 
                                        emp_id=emp_id, 
                                        prefixo=dados_linha['prefixo'], 
                                        incluir_saudacao=False
                                    )
                                    
                                    st.session_state.analises_halley.append({
                                        "id": id_chamado,
                                        "veiculo": dados_linha['prefixo'],
                                        "relato": dados_linha['descricao'],
                                        "parecer": diag
                                    })

                                    if "mensagens_chat_halley" not in st.session_state:
                                        st.session_state.mensagens_chat_halley = []
                                        
                                    st.session_state.mensagens_chat_halley.append({
                                        "role": "assistant",
                                        "content": f"📌 **Análise Veículo {dados_linha['prefixo']}** ({dados_linha['descricao']}):\n\n{diag}"
                                    })
                                    
                                    st.session_state.chat_aberto_usuario = True
                                    st.rerun()

                        elif campos.get("Aprovar") is False:
                            st.session_state.analises_halley = [a for a in st.session_state.analises_halley if a["id"] != id_chamado]

            ed_c = st.data_editor(
                st.session_state.df_ap_work, 
                hide_index=True, 
                use_container_width=True, 
                column_config={
                    "Aprovar": st.column_config.CheckboxColumn("Aprovar?", width="small"), 
                    "prefixo": st.column_config.TextColumn("Prefixo", width="small"),
                    "descricao": st.column_config.TextColumn("Descrição", width="large"),
                    "motorista": st.column_config.TextColumn("Solicitante", width="medium"),
                    "Tipo_OS": st.column_config.SelectboxColumn("Tipo de OS", options=LISTA_TIPOS_OS, width="medium"),
                    "Area_Destino": st.column_config.SelectboxColumn("Área", options=ORDEM_AREAS, width="medium"), 
                    "Data_Programada": st.column_config.DateColumn("Data Programada", width="medium"), 
                    "data_solicitacao": None, 
                    "id": None
                }, 
                key="editor_chamados"
            )
            
            if st.button("Processar Agendamentos", type="primary", key="btn_proc_agendamentos"):
                selecionados = ed_c[ed_c['Aprovar'] == True]
                
                if not selecionados.empty:
                    with engine.connect() as conn:
                        for _, r in selecionados.iterrows():
                            v_os = obter_proxima_os(engine, emp_id)
                            h_prox, o_prox = obter_medidor_proximo(engine, emp_id, r['prefixo'], r['Data_Programada'])
                            desc_com_med = f"{r['descricao']} | [Leitura Ref: Horímetro {h_prox}h, Odômetro {o_prox}km]"
                            
                            conn.execute(
                                text("INSERT INTO tarefas (data, executor, prefixo, inicio_disp, fim_disp, descricao, area, tipo_os, turno, id_chamado, origem, empresa_id, numero_os) VALUES (:dt, :ex, :pr, :ti, :tf, :ds, :ar, :tp, 'Não definido', :ic, 'Chamado', :eid, :nos)"), 
                                {
                                    "dt": str(r['Data_Programada']), "ex": r['Executor'], "pr": r['prefixo'], 
                                    "ti": r['Inicio'], "tf": r['Fim'], "ds": desc_com_med, "ar": r['Area_Destino'], 
                                    "tp": r['Tipo_OS'], "ic": r['id'], "eid": str(emp_id), "nos": v_os
                                }
                            )
                            conn.execute(text("UPDATE chamados SET status = 'Agendado' WHERE id = :id AND empresa_id = :eid"), {"id": int(r['id']), "eid": str(emp_id)})
                        conn.commit()
                    
                    if 'df_ap_work' in st.session_state: del st.session_state.df_ap_work
                    if 'analises_halley' in st.session_state: del st.session_state.analises_halley
                        
                    st.success("✅ Agendamentos processados e enviados à Agenda Principal!")
                    st.rerun()
                else:
                    st.warning("⚠️ Selecione ao menos um chamado na coluna 'Aprovar?' antes de processar.")
        else: 
            st.info("Nenhum chamado pendente no momento.")
            
    elif "Chat Mr. Halley" in aba_ativa:
        st.subheader("🤖 Conversar com Mr. Halley - Telemetria & IA")
        st.caption("Tire dúvidas técnicas sobre falhas, consulte históricos ou solicite a abertura de OS diretamente pelo chat.")

        URL_AVATAR_HALLEY = "https://i.postimg.cc/5tBtrL6C/Whats-App-Image-2026-07-23-at-22-35-53.png"

        if "mensagens_chat_halley" not in st.session_state:
            st.session_state.mensagens_chat_halley = [
                {
                    "role": "assistant",
                    "content": "Olá! Sou o Mr. Halley, seu assistente técnico de manutenção. Como posso te ajudar com os veículos da frota hoje?"
                }
            ]

        for msg in st.session_state.mensagens_chat_halley:
            avatar = URL_AVATAR_HALLEY if msg["role"] == "assistant" else None
            with st.chat_message(msg["role"], avatar=avatar):
                st.markdown(msg["content"])

        if prompt_user := st.chat_input("Digite um sintoma, dúvida técnica ou peça para abrir uma OS..."):
            st.session_state.mensagens_chat_halley.append({"role": "user", "content": prompt_user})
            with st.chat_message("user"):
                st.markdown(prompt_user)

            with st.chat_message("assistant", avatar=URL_AVATAR_HALLEY):
                with st.spinner("Mr. Halley processando..."):
                    resposta = responder_chat_mr_halley(prompt_user, emp_id)
                    st.markdown(resposta)
            
            st.session_state.mensagens_chat_halley.append({"role": "assistant", "content": resposta})
            st.rerun()
            
    elif "Indicadores" in aba_ativa:
        st.subheader("📊 Painel de Performance Operacional")
        st.info("💡 **Dica:** Utilize esses dados para identificar gargalos e planejar a capacidade da oficina.")
        
        query_ind = text("SELECT area, realizado, data, inicio_disp, fim_disp FROM tarefas WHERE empresa_id = :eid")
        df_ind = pd.read_sql(query_ind, engine, params={"eid": str(emp_id)})
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Serviços por Área**")
            st.caption("Mapeia quais setores da oficina (Mecânica, Elétrica, Borracharia, etc.) estão recebendo mais ordens de serviço.")
            if not df_ind.empty:
                st.bar_chart(df_ind['area'].value_counts(), color=COR_OURO) 
            else:
                st.caption("Nenhum dado encontrado.")
                
        with c2: 
            if not df_ind.empty:
                df_st = df_ind['realizado'].map({True: 'Concluído', False: 'Pendente'}).value_counts()
                st.markdown("**Status de Conclusão**")
                st.caption("Mostra a proporção entre os serviços que já receberam baixa técnica (Concluídos) e os que ainda estão na fila (Pendentes).")
                st.bar_chart(df_st, color=COR_OURO) 
                
        st.divider() 
        
        st.markdown("**Desempenho de Lead Time**")
        
        if not df_ind.empty:
            try:
                df_ind['data_dt'] = pd.to_datetime(df_ind['data'], errors='coerce')
                df_ind['Mês'] = df_ind['data_dt'].dt.to_period('M').astype(str)
                
                df_ind['h_inicio'] = pd.to_timedelta(df_ind['inicio_disp'] + ':00', errors='coerce')
                df_ind['h_fim'] = pd.to_timedelta(df_ind['fim_disp'] + ':00', errors='coerce')
                df_ind['lead_time_horas'] = (df_ind['h_fim'] - df_ind['h_inicio']).dt.total_seconds() / 3600
                
                df_valid = df_ind[df_ind['lead_time_horas'] >= 0]
                
                if not df_valid.empty:
                    media_geral_horas = df_valid['lead_time_horas'].mean()
                    
                    col_metrica, col_grafico = st.columns([0.3, 0.7])
                    
                    with col_metrica:
                        if media_geral_horas >= 24:
                            media_geral_dias = media_geral_horas / 24
                            st.metric(label="Média Geral de Retenção", value=f"{media_geral_dias:.1f} Dias")
                        else:
                            st.metric(label="Média Geral de Retenção", value=f"{media_geral_horas:.1f} Horas")
                        st.caption("Tempo médio total que um veículo passa retido em manutenção, do início à baixa técnica.")
                            
                    with col_grafico:
                        st.caption("Evolução Mensal (Média de Horas)")
                        st.caption("Histórico do tempo de retenção ao longo dos meses para avaliar o ganho de eficiência da equipe.")
                        df_lead_time = df_valid.groupby('Mês')['lead_time_horas'].mean().reset_index()
                        df_lead_time = df_lead_time.set_index('Mês')
                        st.line_chart(df_lead_time, color="#C5A059")
                else:
                    st.warning("Aguardando registros com horários válidos para calcular o Lead Time.")
                    
            except Exception as e:
                st.error(f"Erro ao processar gráfico de evolução: {e}")
        else:
            st.warning("Sem dados de tarefas disponíveis para calcular indicadores de evolução.")

    elif "Minha Equipe" in aba_ativa:
        if usuario_ativo != "bruno":
            st.error("🚫 Acesso restrito apenas ao Usuário Master.")
            st.stop()
            
        st.subheader("👥 Gestão de Equipe e Acessos")
        st.info("💡 **Segurança:** As senhas são criptografadas e não podem ser lidas por ninguém. Para alterar a senha de um integrante, use o formulário de redefinição abaixo.")
        
        col_cad, col_reset = st.columns(2)

        with col_cad:
            with st.expander("➕ Novo Integrante", expanded=True):
                with st.form("f_u", clear_on_submit=True):
                    u = st.text_input("Login")
                    s = st.text_input("Senha", type="password")
                    p = st.selectbox("Cargo", ["motorista", "admin"])
                    if st.form_submit_button("Criar Acesso"):
                        if u and s:
                            senha_hash = gerar_hash_senha(s)
                            with engine.connect() as conn:
                                conn.execute(text("INSERT INTO usuarios (login, senha, perfil, empresa_id) VALUES (:u, :s, :p, :eid)"), 
                                             {"u": u.lower().strip(), "s": senha_hash, "p": p, "eid": str(emp_id)})
                                conn.commit()
                            st.success("Acesso criado com sucesso!")
                            st.rerun()
                        else:
                            st.warning("Preencha todos os campos.")

        with col_reset:
            with st.expander("🔑 Redefinir Senha de Integrante", expanded=True):
                df_users_list = pd.read_sql(text("SELECT login FROM usuarios WHERE empresa_id = :eid ORDER BY login ASC"), engine, params={"eid": str(emp_id)})
                lista_logins = df_users_list['login'].tolist() if not df_users_list.empty else []
                
                with st.form("form_reset_senha", clear_on_submit=True):
                    user_alvo = st.selectbox("Selecionar Usuário", lista_logins) if lista_logins else None
                    nova_senha_input = st.text_input("Nova Senha", type="password")
                    
                    if st.form_submit_button("Atualizar Senha"):
                        if user_alvo and nova_senha_input:
                            novo_hash = gerar_hash_senha(nova_senha_input)
                            with engine.connect() as conn:
                                conn.execute(
                                    text("UPDATE usuarios SET senha = :p WHERE login = :u AND empresa_id = :eid"),
                                    {"p": novo_hash, "u": user_alvo, "eid": str(emp_id)}
                                )
                                conn.commit()
                            st.success(f"Senha de **{user_alvo}** alterada com sucesso!")
                        else:
                            st.warning("Selecione o usuário e digite a nova senha.")
                    
        st.divider()
        st.subheader("Integrantes Cadastrados")
        
        df_users = pd.read_sql(text("SELECT id, login, perfil as cargo FROM usuarios WHERE empresa_id = :eid"), engine, params={"eid": str(emp_id)})
        
        if not df_users.empty:
            df_users['Exc'] = False
            ed_users = st.data_editor(
                df_users[['Exc', 'login', 'cargo', 'id']], 
                hide_index=True, 
                use_container_width=True, 
                column_config={
                    "id": None, 
                    "Exc": st.column_config.CheckboxColumn("Excluir", width="small"), 
                    "cargo": st.column_config.SelectboxColumn("Cargo", options=["motorista", "admin"])
                }, 
                key="editor_equipe"
            )
            
            if st.button("🗑️ Excluir Selecionados da Equipe"):
                usuarios_para_deletar = ed_users[ed_users['Exc'] == True]['id'].tolist()
                if usuarios_preventivas_para_deletar := usuarios_para_deletar:
                    with engine.connect() as conn:
                        for u_id in usuarios_para_deletar: 
                            conn.execute(text("DELETE FROM usuarios WHERE id = :id AND empresa_id = :eid"), {"id": int(u_id), "eid": str(emp_id)})
                        conn.commit()
                    st.warning("Integrantes removidos.")
                    time_module.sleep(1)
                    st.rerun()

            if st.session_state.editor_equipe.get("edited_rows"):
                COLUNAS_PERMITIDAS_USUARIOS = {"perfil": "perfil", "login": "login", "cargo": "perfil"}
                with engine.connect() as conn:
                    for idx, changes in st.session_state.editor_equipe["edited_rows"].items():
                        uid = int(df_users.iloc[idx]['id'])
                        for col, val in changes.items():
                            col_real = COLUNAS_PERMITIDAS_USUARIOS.get(col)
                            if col_real == "perfil":
                                conn.execute(text("UPDATE usuarios SET perfil = :v WHERE id = :i AND empresa_id = :eid"), {"v": str(val).strip(), "i": uid, "eid": str(emp_id)})
                            elif col_real == "login":
                                conn.execute(text("UPDATE usuarios SET login = :v WHERE id = :i AND empresa_id = :eid"), {"v": str(val).lower().strip(), "i": uid, "eid": str(emp_id)})
                    conn.commit()
                st.rerun()

if st.session_state.get("logado") and "empresa" in st.session_state:
    if "Chat Mr. Halley" not in st.session_state.get("opcao_selecionada", ""):
        renderizar_chat_flutuante(st.session_state["empresa"])
