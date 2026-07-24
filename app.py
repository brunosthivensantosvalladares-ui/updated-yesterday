import streamlit as st
import pandas as pd
import os
from sqlalchemy import create_engine, text
from datetime import datetime, time, timedelta
from io import BytesIO
from fpdf import FPDF
from google import genai
import time as time_module
import requests

# --- 1. CONFIGURAÇÕES E ESTILOS ---
NOME_SISTEMA = "Updated Yesterday"
SLOGAN = "Seu Controle. Nossa Prioridade."
LOGO_URL = "https://i.postimg.cc/6Q7dyFgs/Gemini-Generated-Image.png"
ORDEM_AREAS = ["Motorista", "Borracharia", "Mecânica", "Elétrica", "Chapeamento", "Limpeza"]
LISTA_TURNOS = ["Não definido", "Dia", "Noite"]

# --- 2. FUNÇÕES DE SUPORTE E BANCO ---
@st.cache_resource
def get_engine():
    db_url = st.secrets.get("database_url") or os.environ.get("database_url")
    if not db_url:
        st.error("Erro crítico: Configuração do banco de dados não encontrada.")
        st.stop()
    return create_engine(db_url.replace("postgres://", "postgresql://", 1), pool_pre_ping=True)

# --- BUSCA DE HISTÓRICO LOCAL (RAG DO MR. HALLEY) ---
def buscar_historico_relevante(sintoma_motorista, emp_id):
    """ Varre as OSs concluídas descartando termos genéricos para não trazer registros irrelevantes. """
    engine = get_engine()
    
    # Lista de palavras ignoradas por serem genéricas demais no contexto automotivo
    palavras_ignoradas = {'carro', 'veiculo', 'caminhao', 'saindo', 'muita', 'muito', 'fazer', 'esta', 'estão'}
    
    palavras = [
        p for p in sintoma_motorista.lower().split() 
        if len(p) > 3 and p not in palavras_ignoradas
    ]
    
    if not palavras:
        return "Nenhum histórico prévio encontrado para termos genéricos."
        
    # Busca dinamicamente apenas pelas palavras-chave reais do problema (ex: 'fumaça', 'preta')
    condicoes = " OR ".join([f"LOWER(descricao) LIKE '%{p}%'" for p in palavras])
    
    query = text(f"""
        SELECT prefixo, descricao 
        FROM tarefas 
        WHERE empresa_id = :eid AND realizado = True AND ({condicoes})
        ORDER BY id DESC LIMIT 3
    """)
    
    try:
        with engine.connect() as conn:
            resultados = conn.execute(query, {"eid": str(emp_id)}).fetchall()
        if not resultados:
            return "Nenhuma Ordem de Serviço concluída anteriormente com sintomas parecidos."
            
        historico_formatado = ""
        for row in resultados:
            historico_formatado += f"- Veículo {row[0]}: {row[1]}\n"
        return historico_formatado
    except Exception as e:
        return f"Sem histórico disponível ({e})."

# --- CONSULTA AO CÉREBRO DO MR. HALLEY (INTEGRADO AO GEMINI FLASH) ---
def triagem_mr_halley(sintoma, emp_id):
    historico = buscar_historico_relevante(sintoma, emp_id)
    gemini_key = st.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    
    if gemini_key:
        try:
            client = genai.Client(api_key=gemini_key)
            prompt = f"""
Você é o Mr. Halley, assistente técnico de manutenção do Up 2 Today.
Com base no histórico: '{historico}' e no sintoma atual: '{sintoma}'.

Gere uma resposta EXTREMAMENTE CURTA (máximo 2 linhas) para o PCM.
Exemplo de formato:
"Histórico indica problema nos bicos injetores. Recomendo teste de vazão e limpeza da alimentação."
"""
            response = client.models.generate_content(
                model='gemini-1.5-flash',
                contents=prompt
            )
            return response.text.strip()
        except Exception:
            pass

    # Fallback ultra resumido (caso a API não responda)
    if historico and "Nenhuma" not in historico:
        return "⚙️ **Histórico indica:** Casos parecidos foram resolvidos com limpeza/troca dos bicos injetores. Recomendo inspeção no sistema de alimentação."
    
    return "⚙️ Sem histórico idêntico registrado. Recomenda-se inspeção geral do sistema."
    
# --- INICIALIZAÇÃO SEGURA DO CLIENTE ---
if "GEMINI_API_KEY" in st.secrets:
    try:
        client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
        st.session_state["gemini_client"] = client
    except Exception as e:
        st.sidebar.error("IA indisponível no momento.")
else:
    st.sidebar.warning("IA não configurada.")

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
        "O Assistente monitora a integridade dos prazos. O alerta visual no topo indica que "
        "há pendências de datas passadas. O botão 'Resolver' permite ao gestor dar "
        "baixa imediata ou reagendar tarefas para o presente com um único clique."
    ))

    texto_pdf = pdf.output(dest='S')
    return texto_pdf.encode('latin-1', 'replace')

# --- LÓGICA DE GERAÇÃO DE OS SEQUENCIAL ---
def obter_proxima_os(engine, emp_id):
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT MAX(numero_os) FROM tarefas WHERE empresa_id = :eid"), {"eid": emp_id}).fetchone()
            maior_os = result[0]
            if maior_os is None:
                return 1001 
            return int(maior_os) + 1
    except:
        return 1001

COR_BRONZE = "#4A3C31"  
COR_OURO = "#C5A059"    
COR_CHAPA = "#E2DFD2"   
COR_TEXTO = "#231F20"   

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title=f"{NOME_SISTEMA} - Painel de Controle", layout="wide", page_icon="⚙️")

st.markdown(f"""
    <style>
    html, body, [data-testid="stAppViewContainer"], .stApp {{ background-color: {COR_CHAPA} !important; }}
    [data-testid="stSidebar"] {{ background-color: #FFFFFF !important; }}

    [data-testid="stSidebarCollapsedControl"] svg, 
    button[data-testid="stBaseButton-headerNoPadding"] svg {{
        fill: {COR_BRONZE} !important;
        color: {COR_BRONZE} !important;
    }}

    p, label, span, div, .stMarkdown, [data-testid="stText"] {{
        color: {COR_TEXTO} !important;
    }}
    
    [data-testid="stSidebar"] p, [data-testid="stSidebar"] label, [data-testid="stSidebar"] span, [data-testid="stSidebar"] div {{
        color: {COR_BRONZE} !important;
    }}

    div[data-testid="stRadio"] > div {{
        display: flex;
        justify-content: center;
        background-color: #FFFFFF;
        padding: 10px;
        border-radius: 6px;
        border: 2px solid {COR_BRONZE};
    }}

    button, 
    button[kind="primary"], 
    button[kind="secondary"], 
    [data-testid="stBaseButton-primary"], 
    [data-testid="stBaseButton-secondary"] {{
        background-color: {COR_BRONZE} !important;
        border: 2px solid {COR_OURO} !important;
        border-radius: 4px !important;
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

    button svg, [data-testid="stDateInput"] svg {{
        fill: #FFFFFF !important;
        color: #FFFFFF !important;
    }}

    div[data-baseweb="calendar"] [aria-selected="true"],
    div[data-baseweb="calendar"] [class*="Selected"],
    div[data-baseweb="calendar"] [class*="Highlighted"] {{
        background-color: {COR_BRONZE} !important;
        background: {COR_BRONZE} !important;
    }}
    </style>
""", unsafe_allow_html=True)

st.markdown("""
    <style>
    .logo-u { color: #4A3C31 !important; font-weight: bold; }
    .logo-y { color: #C5A059 !important; font-weight: bold; }
    .login-brand-title {
        text-align: center !important;
        color: #C5A059 !important; 
        font-size: 2.8rem !important;
        font-weight: 900 !important;
        margin-bottom: 0px !important;
        margin-top: 10px !important;
        line-height: 1.1 !important;
        font-family: sans-serif !important;
    }
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
            conn.execute(text("CREATE TABLE IF NOT EXISTS tarefas (id SERIAL PRIMARY KEY, data TEXT, executor TEXT, prefixo TEXT, inicio_disp TEXT, fim_disp TEXT, descricao TEXT, area TEXT, turno TEXT, realizado BOOLEAN DEFAULT FALSE, id_chamado INTEGER, origem TEXT, empresa_id TEXT)"))
            conn.execute(text("CREATE TABLE IF NOT EXISTS chamados (id SERIAL PRIMARY KEY, motorista TEXT, prefixo TEXT, descricao TEXT, data_solicitacao TEXT, status TEXT DEFAULT 'Pendente', empresa_id TEXT)"))
            conn.execute(text("ALTER TABLE tarefas ADD COLUMN IF NOT EXISTS numero_os INTEGER"))
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
            except: pass
            try: conn.execute(text("ALTER TABLE chamados ADD COLUMN IF NOT EXISTS empresa_id TEXT DEFAULT 'U2T_MATRIZ'"))
            except: pass
            conn.commit()
    except: pass

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
                
    return pdf.output(dest='S').encode('latin-1')

# --- INICIALIZAÇÃO DE ESTADOS DE SESSÃO ---
if "logado" not in st.session_state:
    st.session_state["logado"] = False

if "perfil" not in st.session_state:
    st.session_state["perfil"] = "motorista"

if "usuario_ativo" not in st.session_state:
    st.session_state["usuario_ativo"] = ""

# ==================================================
# 1. TELA DE ACESSO (Se o usuário NÃO estiver logado)
# ==================================================
if not st.session_state["logado"]:
    _, col_login, _ = st.columns([1.2, 1, 1.2])
    with col_login:
        st.markdown("<p class='login-brand-title'>UY</p>", unsafe_allow_html=True)
        st.markdown(f"<p style='text-align: center; font-style: italic; color: #555; margin-top: 0;'>{SLOGAN}</p>", unsafe_allow_html=True)
        
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
                                    check_user = conn.execute(text("SELECT id FROM usuarios WHERE LOWER(login) = 'bruno'")).fetchone()
                                    if check_user:
                                        conn.execute(text("UPDATE usuarios SET senha = :p, perfil = 'admin', empresa_id = 'U2T_MATRIZ' WHERE LOWER(login) = 'bruno'"), {"p": pw_input})
                                    else:
                                        conn.execute(text("INSERT INTO usuarios (login, senha, perfil, empresa_id) VALUES ('bruno', :p, 'admin', 'U2T_MATRIZ')"), {"p": pw_input})
                                    conn.commit()
                            except Exception as e:
                                pass

                        with engine.connect() as conn:
                            empresa = conn.execute(
                                text("""
                                    SELECT nome, senha FROM empresa 
                                    WHERE LOWER(TRIM(email)) = LOWER(TRIM(:u)) 
                                       OR LOWER(TRIM(nome)) = LOWER(TRIM(:u))
                                """), 
                                {"u": user_input}
                            ).fetchone()
                        
                        if empresa and empresa[1].strip() == pw_input:
                            st.session_state["logado"] = True
                            st.session_state["empresa"] = empresa[0]
                            st.session_state["perfil"] = "admin"
                            st.session_state["usuario_ativo"] = user_input
                            st.success("✅ Login efetuado com sucesso!")
                            st.rerun()
                        
                        else:
                            with engine.connect() as conn:
                                usuario = conn.execute(
                                    text("""
                                        SELECT empresa_id, perfil, senha FROM usuarios 
                                        WHERE LOWER(TRIM(login)) = LOWER(TRIM(:u))
                                    """), 
                                    {"u": user_input}
                                ).fetchone()
                                
                            if usuario and usuario[2].strip() == pw_input:
                                st.session_state["logado"] = True
                                st.session_state["empresa"] = usuario[0]
                                st.session_state["perfil"] = usuario[1]
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
                            with engine.connect() as conn:
                                conn.execute(text("INSERT INTO empresa (nome, email, senha, data_expiracao) VALUES (:n, :e, :s, :d)"), {"n": n_emp, "e": n_ema, "s": n_sen, "d": expira})
                                conn.commit()
                            st.success("✅ Conta criada! Agora faça login na aba 'Acessar'.")
                        except Exception as e:
                            st.error("Este e-mail já está cadastrado.")
                    else:
                        st.warning("Preencha todos os campos.")

# ==================================================
# 2. AMBIENTE LOGADO (Aparece se st.session_state["logado"] == True)
# ==================================================
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
        opcoes = ["✍️ Abrir Solicitação", "📜 Status"]
    else:
        opcoes = ["📅 Agenda Principal", "📋 Cadastro Direto", "📥 Chamados Oficina", "⏳ OSs Pendentes", "✅ OSs Concluídas", "📊 Indicadores", "📖 Manual do Sistema"]
        
        if usuario_ativo == "bruno":
            opcoes.insert(6, "👥 Minha Equipe")
            opcoes.append("👑 Gestão Master")

    if "opcao_selecionada" not in st.session_state or st.session_state.opcao_selecionada not in opcoes:
        st.session_state.opcao_selecionada = opcoes[0]
    
    if "radio_key" not in st.session_state:
        st.session_state.radio_key = 0

    def set_nav(target):
        st.session_state.opcao_selecionada = target
        st.session_state.radio_key += 1 

    # --- MONTAGEM DA SIDEBAR ---
    with st.sidebar:
        _, col_img, _ = st.columns([0.05, 0.9, 0.05])
        with col_img:
            st.image(LOGO_URL, use_container_width=True)
        st.markdown(f"<p style='text-align: center; font-size: 0.8rem; color: {COR_BRONZE}; margin-top: -10px;'>{SLOGAN}</p>", unsafe_allow_html=True)
        st.divider()
        
        try:
            idx_seguro = opcoes.index(st.session_state.opcao_selecionada)
        except ValueError:
            idx_seguro = 0; st.session_state.opcao_selecionada = opcoes[0]

        escolha_sidebar = st.radio(
            "NAVEGAÇÃO", 
            opcoes, 
            index=idx_seguro,
            key=f"radio_nav_{st.session_state.radio_key}",
            on_change=lambda: st.session_state.update({"opcao_selecionada": st.session_state[f"radio_nav_{st.session_state.radio_key}"]})
        )
        
        st.divider()
        st.write(f"🏢 **Empresa:** {emp_id}")
        st.write(f"👤 **{st.session_state['perfil'].capitalize()}**")
        if st.button("Sair da Conta", type="primary"): 
            st.session_state["logado"] = False
            st.rerun()

    # --- BOTÕES DE NAVEGAÇÃO DE ABA NO TOPO ---
    cols = st.columns(len(opcoes))
    for i, nome in enumerate(opcoes):
        eh_ativo = nome == st.session_state.opcao_selecionada
        cols[i].button(
            nome, 
            key=f"btn_tab_{i}", 
            use_container_width=True, 
            type="primary" if eh_ativo else "secondary",
            on_click=set_nav, 
            args=(nome,)
        )

    st.divider()
    aba_ativa = st.session_state.opcao_selecionada

    # --- 3. CONTEÚDO DAS PÁGINAS ---
    if aba_ativa == "👑 Gestão Master" and usuario_ativo == "bruno":
        st.subheader("👑 Painel de Controle Master")
        
        if "gemini_client" in st.session_state:
            if st.button("✨ Sugerir Manutenção com IA"):
                try:
                    prompt = "O motorista relatou barulho na suspensão do veículo X. O que pode ser?"
                    response = st.session_state["gemini_client"].models.generate_content(
                        model='gemini-1.5-flash', 
                        contents=prompt
                    )
                    st.write(response.text)
                except Exception as e:
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

    elif aba_ativa == "✍️ Abrir Solicitação":
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

    elif aba_ativa == "📜 Status":
        st.subheader("📜 Status dos Meus Veículos")
        st.info("Aqui você pode ver se o seu veículo já foi agendado ou concluído pela oficina.")
        df_status = pd.read_sql(text("SELECT prefixo, data_solicitacao as data, status, descricao FROM chamados WHERE empresa_id = :eid ORDER BY id DESC"), engine, params={"eid": emp_id})
        st.dataframe(df_status, use_container_width=True, hide_index=True)
    
    elif aba_ativa == "📖 Manual do Sistema":
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
            except:
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

    elif aba_ativa == "⏳ OSs Pendentes":
        if 'os_em_baixa' not in st.session_state:
            st.session_state.os_em_baixa = None

        if st.session_state.os_em_baixa is not None:
            os_data = st.session_state.os_em_baixa
            os_num = str(os_data['numero_os']).split('.')[0]
            
            st.button("⬅️ Voltar para a Lista", on_click=lambda: setattr(st.session_state, 'os_em_baixa', None))
            st.subheader(f"⚡ Baixa Técnica: OS {os_num}")
            with st.container(border=True):
                st.write(f"🚜 **Veículo:** {os_data['prefixo']}")
                st.write(f"📝 **Serviço Planejado:** {os_data['descricao']}")
                
                with st.form("form_baixa_exclusiva"):
                    servico_realizado = st.text_area("O que foi feito de fato?", placeholder="Descreva a execução...")
                    executor = st.text_input("Mecânico Responsável")
                    c1, c2 = st.columns(2)
                    h_ini = c1.text_input("Início", "08:00")
                    h_fim = c2.text_input("Fim", "10:00")

                    if st.form_submit_button("💾 Finalizar e Salvar"):
                        if not servico_realizado:
                            st.error("A descrição do serviço é obrigatória.")
                        else:
                            relato = f"Execução: {servico_realizado}; Mecânico: {executor}; Horário: {h_ini}-{h_fim}"
                            with engine.begin() as conn:
                                query_update = text("""
                                    UPDATE tarefas 
                                    SET realizado = True, 
                                        descricao = 'OS: ' || :os || '; Prefixo: ' || :pref || '; ' || COALESCE(descricao, '') || '; ' || :relato
                                    WHERE id = :id_banco 
                                    AND empresa_id = :eid
                                """)
                                conn.execute(query_update, {
                                    "relato": str(relato),
                                    "os": str(os_num),
                                    "pref": str(os_data['prefixo']),
                                    "id_banco": int(os_data['id']),
                                    "eid": str(emp_id)
                                })
                            st.cache_data.clear()
                            st.session_state.os_em_baixa = None
                            st.success(f"✅ OS {os_num} finalizada com sucesso!")
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
    
    elif aba_ativa == "✅ OSs Concluídas":
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
                AND (TRIM(CAST(empresa_id AS TEXT)) = TRIM(:eid) OR empresa_id IS NULL)
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
            
    elif aba_ativa == "📅 Agenda Principal":
        st.subheader("📅 Cronograma Geral de Manutenções")
        
        try:
            df_stats = pd.read_sql(text("SELECT data, realizado FROM tarefas WHERE empresa_id = :eid"), engine, params={"eid": emp_id})
            if not df_stats.empty:
                df_stats['data'] = pd.to_datetime(df_stats['data']).dt.date
                hoje_dt = datetime.now().date()
                df_hoje = df_stats[df_stats['data'] == hoje_dt]
                
                m1, m2, m3 = st.columns(3)
                with m1: st.metric("Agendados Hoje", len(df_hoje))
                with m2: st.metric("Concluídos", len(df_hoje[df_hoje['realizado'] == True]))
                with m3: st.metric("Pendentes", len(df_hoje[df_hoje['realizado'] == False]))
                st.divider()
        except:
            st.warning("⚠️ O banco de dados está iniciando. Aguarde alguns segundos.")
            st.stop()

        try:
            query = text("SELECT numero_os, data, prefixo, descricao, realizado FROM tarefas WHERE empresa_id = :eid ORDER BY data DESC")
            df_agenda = pd.read_sql(query, engine, params={"eid": str(emp_id)})
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
                    0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(255, 75, 75, 0.7); }
                    70% { transform: scale(1); box-shadow: 0 0 0 10px rgba(255, 75, 75, 0); }
                    100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(255, 75, 75, 0); }
                }
            </style>
        """, unsafe_allow_html=True)

        df_atrasadas = pd.read_sql(text("SELECT * FROM tarefas WHERE data < :hoje AND realizado = False AND empresa_id = :eid"), 
                                   engine, params={"hoje": str(datetime.now().date()), "eid": emp_id})

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
                                    conn.execute(text("UPDATE tarefas SET realizado=True WHERE data < :hoje AND realizado=False AND empresa_id=:eid"), {"hoje":str(datetime.now().date()), "eid":emp_id})
                                    conn.commit()
                                st.cache_data.clear()
                                st.rerun()

                            if c2.button("📅 Trazer p/ Hoje", use_container_width=True, key="mini_today"):
                                with engine.connect() as conn:
                                    conn.execute(text("UPDATE tarefas SET data=:hoje WHERE data < :hoje AND realizado=False AND empresa_id=:eid"), {"hoje":str(datetime.now().date()), "eid":emp_id})
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
                                        st.session_state.opcao_selecionada = "⏳ OSs Pendentes"
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
        
        df_a = pd.read_sql(text("SELECT * FROM tarefas WHERE empresa_id = :eid ORDER BY data DESC"), engine, params={"eid": emp_id})
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
                                        WHERE id = :id
                                    """), {
                                        "r": bool(row['realizado']), "ar": str(row['area']), "t": str(row['turno']), 
                                        "p": str(row['prefixo']), "i": str(row['inicio_disp']), 
                                        "f": str(row['fim_disp']), "ex": str(row['executor']), 
                                        "ds": str(row['descricao']), "id": int(row_id)
                                    })
                                    if row['realizado'] and pd.notnull(row['id_chamado']):
                                        try: conn.execute(text("UPDATE chamados SET status = 'Concluído' WHERE id = :ic"), {"ic": int(row['id_chamado'])})
                                        except: pass
                                conn.commit()
                            st.toast("Alteração salva!", icon="✅")
                            time_module.sleep(0.5); st.rerun()

    elif aba_ativa == "📋 Cadastro Direto":
        st.subheader("📝 Agendamento Direto")
        with st.popover("💡 Como usar o Cadastro Direto?"):
            st.markdown("""
                ### 📝 Guia Rápido - Cadastro
                1. **Uso:** Utilize para preventivas ou serviços que não vieram de uma reclamação de motorista.
                2. **Formulário:** Preencha os campos e confirme.
                3. **Gestão:** Na lista abaixo, você pode excluir registros marcando a coluna **Exc** e clicando em excluir.
            """)
        st.info("💡 **Atenção:** Use este formulário para serviços que não vieram de chamados.")
        st.warning("⚠️ **Nota:** Para reagendar ou corrigir, basta alterar diretamente na lista abaixo. O salvamento é automático.")
        
        with st.form("f_d", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns(4)
            with c1: d_i = st.date_input("Data", datetime.now())
            with c2: e_i = st.text_input("Executor")
            with c3: p_i = st.text_input("Prefixo")
            with c4: a_i = st.selectbox("Área", ORDEM_AREAS)
            c5, c6 = st.columns(2)
            with c5: t_ini = st.text_input("Início (Ex: 08:00)", "00:00")
            with c6: t_fim = st.text_input("Fim (Ex: 10:00)", "00:00")
            ds_i, t_i = st.text_area("Descrição"), st.selectbox("Turno", LISTA_TURNOS)
            if st.form_submit_button("Confirmar Agendamento"):
                nova_os = obter_proxima_os(engine, emp_id)
                with engine.connect() as conn:
                    conn.execute(text("INSERT INTO tarefas (data, executor, prefixo, inicio_disp, fim_disp, descricao, area, turno, origem, empresa_id, numero_os) VALUES (:dt, :ex, :pr, :ti, :tf, :ds, :ar, :tu, 'Direto', :eid, :nos)"), 
                                 {"dt": str(d_i), "ex": e_i, "pr": p_i, "ti": t_ini, "tf": t_fim, "ds": ds_i, "ar": a_i, "tu": t_i, "eid": emp_id, "nos": nova_os})
                    conn.commit()
                st.success(f"✅ SERVIÇO AGENDADO!")
                st.code(f"NÚMERO DA ORDEM DE SERVIÇO: {nova_os}", language="markdown")
                st.rerun()
        
        st.divider()
        st.subheader("📋 Lista de serviços")
        df_lista = pd.read_sql(text("SELECT * FROM tarefas WHERE empresa_id = :eid ORDER BY data DESC, id DESC"), engine, params={"eid": emp_id})
        
        if not df_lista.empty:
            df_lista['data'] = pd.to_datetime(df_lista['data']).dt.date
            df_lista['Exc'] = False
            ed_l = st.data_editor(df_lista[['Exc', 'data', 'turno', 'executor', 'prefixo', 'inicio_disp', 'fim_disp', 'descricao', 'area', 'id']], hide_index=True, use_container_width=True, key="ed_lista")
            
            if st.button("🗑️ Excluir Selecionados"):
                with engine.connect() as conn:
                    for i in ed_l[ed_l['Exc']==True]['id'].tolist(): 
                        conn.execute(text("DELETE FROM tarefas WHERE id = :id"), {"id": int(i)})
                    conn.commit()
                st.warning("🗑️ Itens excluídos.")
                st.rerun()
                
            if st.session_state.ed_lista["edited_rows"]:
                with engine.connect() as conn:
                    for idx, changes in st.session_state.ed_lista["edited_rows"].items():
                        rid = int(df_lista.iloc[idx]['id'])
                        for col, val in changes.items():
                            if col != 'Exc': 
                                conn.execute(text(f"UPDATE tarefas SET {col} = :v WHERE id = :i"), {"v": str(val), "i": rid})
                    conn.commit()
                st.rerun()

    elif aba_ativa == "📥 Chamados Oficina":
        c_tit, c_refresh = st.columns([0.8, 0.2])
        with c_tit: 
            st.subheader("📥 Aprovação de Chamados")
            
        with st.popover("💡 Como usar os Chamados?"):
            st.markdown("""
                ### 📥 Guia Rápido - Chamados
                1. **Triagem:** Veja o que os motoristas relataram. 
                2. **Aprovação:** Marque a caixa **Aprovar?** e o Mr. Halley dará o diagnóstico na hora!
                3. **Planejamento:** Defina o Executor e a Área com base no parecer da IA.
                4. **Finalizar:** Clique em **Processar Agendamentos**.
            """)
            
        with c_refresh:
            if st.button("🔄 Atualizar Lista", use_container_width=True, key="btn_refresh_chamados"):
                if 'df_ap_work' in st.session_state: 
                    del st.session_state.df_ap_work
                if 'analise_imediata_halley' in st.session_state:
                    del st.session_state.analise_imediata_halley
                st.rerun()
            
        df_p = pd.read_sql(text("SELECT id, data_solicitacao, motorista, prefixo, descricao FROM chamados WHERE status = 'Pendente' AND empresa_id = :eid ORDER BY id DESC"), engine, params={"eid": emp_id})
        
        if not df_p.empty:
            if 'df_ap_work' not in st.session_state:
                df_p['Executor'], df_p['Area_Destino'], df_p['Data_Programada'], df_p['Inicio'], df_p['Fim'], df_p['Aprovar'] = "", "Mecânica", datetime.now().date(), "00:00", "00:00", False
                st.session_state.df_ap_work = df_p
            
            # --- LÓGICA DE DETECÇÃO EM TEMPO REAL ---
            if "editor_chamados" in st.session_state and st.session_state.editor_chamados.get("edited_rows"):
                alteracoes = st.session_state.editor_chamados["edited_rows"]
                for c_idx, campos in alteracoes.items():
                    if campos.get("Aprovar") is True:
                        idx_real = int(c_idx)
                        if idx_real < len(st.session_state.df_ap_work):
                            dados_linha = st.session_state.df_ap_work.iloc[idx_real]
                            id_chamado = dados_linha['id']
                            
                            if st.session_state.get("last_analised_id") != id_chamado:
                                # Força a limpeza do cache antigo para não misturar diagnósticos
                                if "analise_imediata_halley" in st.session_state:
                                    del st.session_state["analise_imediata_halley"]
                                    
                                with st.spinner("🤖 Mr. Halley analisando prontuários para esta seleção..."):
                                    diag = triagem_mr_halley(dados_linha['descricao'], emp_id)
                                    st.session_state["analise_imediata_halley"] = {
                                        "veiculo": dados_linha['prefixo'],
                                        "relato": dados_linha['descricao'],
                                        "parecer": diag
                                    }
                                    st.session_state["last_analised_id"] = id_chamado
                    
                    # Se desmarcar a caixa, limpa a tela imediatamente
                    elif campos.get("Aprovar") is False:
                        if "analise_imediata_halley" in st.session_state:
                            del st.session_state["analise_imediata_halley"]
                        if "last_analised_id" in st.session_state:
                            del st.session_state["last_analised_id"]

            # --- EXIBIÇÃO DO CHAT DO MR. HALLEY EM TEMPO REAL ---
            if "analise_imediata_halley" in st.session_state:
                res = st.session_state["analise_imediata_halley"]
                
                URL_AVATAR_HALLEY = "https://i.postimg.cc/5tBtrL6C/Whats-App-Image-2026-07-23-at-22-35-53.png"
                
                # Mascote ampliado para 180px para maior destaque visual
                html_layout = f"""
                <div style="display: flex; align-items: center; justify-content: flex-end; margin: 25px 0; font-family: sans-serif;">
                    <div style="background-color: #FFFFFF; border: 2px solid #C5A059; border-radius: 16px; padding: 18px 22px; margin-right: 20px; max-width: 75%; color: #1E293B; box-shadow: 0 4px 12px rgba(0,0,0,0.08);">
                        <strong style="color: #4A3C31; font-size: 1.15em; display: block; margin-bottom: 6px;">🤖 Telemetria do Mr. Halley</strong>
                        <span style="color: #64748B; font-size: 0.88em; display: block; margin-bottom: 8px; font-weight: 600;">Veículo: {res['veiculo']}</span>
                        <p style="margin: 0; font-size: 0.98em; line-height: 1.5; color: #1E293B;">
                            <strong style="color: #4A3C31;">Parecer Técnico:</strong> {res['parecer']}
                        </p>
                    </div>
                    <div style="flex-shrink: 0; text-align: center;">
                        <img src="{URL_AVATAR_HALLEY}" style="width: 180px; height: auto;" alt="Mr. Halley">
                    </div>
                </div>
                """
                
                st.markdown(html_layout, unsafe_allow_html=True)
                st.markdown("---")
            
            ed_c = st.data_editor(
                st.session_state.df_ap_work, 
                hide_index=True, 
                use_container_width=True, 
                column_config={
                    "data_solicitacao": "Aberto em", 
                    "motorista": "Solicitante", 
                    "Data_Programada": st.column_config.DateColumn("Data Programada"), 
                    "Area_Destino": st.column_config.SelectboxColumn("Área", options=ORDEM_AREAS), 
                    "Aprovar": st.column_config.CheckboxColumn("Aprovar?"), 
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
                            conn.execute(
                                text("INSERT INTO tarefas (data, executor, prefixo, inicio_disp, fim_disp, descricao, area, turno, id_chamado, origem, empresa_id, numero_os) VALUES (:dt, :ex, :pr, :ti, :tf, :ds, :ar, 'Não definido', :ic, 'Chamado', :eid, :nos)"), 
                                {"dt": str(r['Data_Programada']), "ex": r['Executor'], "pr": r['prefixo'], "ti": r['Inicio'], "tf": r['Fim'], "ds": r['descricao'], "ar": r['Area_Destino'], "ic": r['id'], "eid": emp_id, "nos": v_os}
                            )
                            conn.execute(text("UPDATE chamados SET status = 'Agendado' WHERE id = :id"), {"id": r['id']})
                        conn.commit()
                    
                    if 'df_ap_work' in st.session_state: del st.session_state.df_ap_work
                    if 'analise_imediata_halley' in st.session_state: del st.session_state.analise_imediata_halley
                    if 'last_analised_id' in st.session_state: del st.session_state.last_analised_id
                        
                    st.success("✅ Agendamentos processados e enviados à Agenda Principal!")
                    st.rerun()
                else:
                    st.warning("⚠️ Selecione ao menos um chamado na coluna 'Aprovar?' antes de processar.")
        else: 
            st.info("Nenhum chamado pendente no momento.")

    elif aba_ativa == "📊 Indicadores":
        st.subheader("📊 Painel de Performance Operacional")
        st.info("💡 **Dica:** Utilize esses dados para identificar gargalos e planejar a capacidade da oficina.")
        
        query_ind = text("SELECT area, realizado, data, inicio_disp, fim_disp FROM tarefas WHERE empresa_id = :eid")
        df_ind = pd.read_sql(query_ind, engine, params={"eid": emp_id})
        
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

    elif aba_ativa == "👥 Minha Equipe":
        if usuario_ativo != "bruno":
            st.error("🚫 Acesso restrito apenas ao Usuário Master.")
            st.stop()
            
        st.subheader("👥 Gestão de Equipe e Acessos")
        st.info("💡 **Dica profissional:** Para editar senhas ou cargos, altere diretamente na tabela. Para excluir, marque 'Exc' e clique no botão abaixo.")
        
        with st.expander("➕ Novo Integrante", expanded=True):
            with st.form("f_u", clear_on_submit=True):
                u, s, p = st.text_input("Login"), st.text_input("Senha"), st.selectbox("Cargo", ["motorista", "admin"])
                if st.form_submit_button("Criar Acesso"):
                    with engine.connect() as conn:
                        conn.execute(text("INSERT INTO usuarios (login, senha, perfil, empresa_id) VALUES (:u, :s, :p, :eid)"), {"u": u.lower(), "s": s, "p": p, "eid": emp_id})
                        conn.commit()
                    st.success("Acesso criado!")
                    st.rerun()
                    
        st.divider()
        st.subheader("Integrantes Cadastrados")
        
        df_users = pd.read_sql(text("SELECT id, login, senha, perfil as cargo FROM usuarios WHERE empresa_id = :eid"), engine, params={"eid": emp_id})
        
        if not df_users.empty:
            df_users['Exc'] = False
            ed_users = st.data_editor(df_users[['Exc', 'login', 'senha', 'cargo', 'id']], hide_index=True, use_container_width=True, column_config={"id": None, "Exc": st.column_config.CheckboxColumn("Excluir", width="small"), "cargo": st.column_config.SelectboxColumn("Cargo", options=["motorista", "admin"])}, key="editor_equipe")
            
            if st.button("🗑️ Excluir Selecionados da Equipe"):
                usuarios_para_deletar = ed_users[ed_users['Exc'] == True]['id'].tolist()
                if usuarios_para_deletar:
                    with engine.connect() as conn:
                        for u_id in usuarios_para_deletar: 
                            conn.execute(text("DELETE FROM usuarios WHERE id = :id"), {"id": int(u_id)})
                        conn.commit()
                    st.warning("Integrantes removidos.")
                    time_module.sleep(1)
                    st.rerun()
