import streamlit as st
from openai import OpenAI
from pinecone import Pinecone
import pypdf
import pandas as pd
import io
import json
import os
import requests
import base64
import datetime
from streamlit_oauth import OAuth2Component
import toolbox

# --- CONFIGURATION (SECURED FOR CLOUD) ---
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
PINECONE_API_KEY = st.secrets["PINECONE_API_KEY"]
PINECONE_INDEX_NAME = "digital-marsh" 

GOOGLE_CLIENT_ID = st.secrets["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = st.secrets["GOOGLE_CLIENT_SECRET"]

# 1. Interface Initialisation (NASA THEME)
st.set_page_config(page_title="Blessed Oracle of Freight", page_icon="🚀", layout="wide")

# --- CUSTOM CSS: BRIGHT NASA CONTROL CENTER ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700;900&display=swap');

    .stApp, p, h1, h2, h3, h4, h5, h6, li, label, input, button, .stMarkdown, div[data-testid="stText"] {
        font-family: 'Poppins', sans-serif !important;
    }

    .stApp {
        background-color: #ffffff;
    }
    .main-header {
        color: #0b3d91; 
        font-weight: 900;
        text-transform: uppercase;
        letter-spacing: 2px;
    }
    .sub-header {
        color: #fc3d21; 
        font-weight: 700;
    }
    .telemetry-header {
        color: #0b3d91;
        font-weight: 700;
        border-bottom: 2px solid #0b3d91;
        padding-bottom: 5px;
        margin-bottom: 15px;
        text-transform: uppercase;
    }
    .status-text {
        font-size: 14px;
        margin-bottom: 8px;
        font-weight: 400;
    }
    
    .stTabs [data-baseweb="tab-list"] {
        gap: 20px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: #f4f6f9;
        border-radius: 4px 4px 0px 0px;
        padding-top: 10px;
        padding-bottom: 10px;
    }
    
    .chat-input-form {
        margin-bottom: 30px;
    }
    </style>
""", unsafe_allow_html=True)

# --- GOOGLE SSO BOUNCER (WITH NATIVE URL PERSISTENCE) ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "_auth" in st.query_params and not st.session_state.logged_in:
    try:
        decoded_email = base64.b64decode(st.query_params["_auth"]).decode()
        if decoded_email.endswith("@freightcompaniesaustralia.com.au"):
            st.session_state.logged_in = True
            st.session_state.user_email = decoded_email
    except Exception:
        pass

if not st.session_state.logged_in:
    st.markdown("<h1 class='main-header'>Blessed Oracle of Freight</h1>", unsafe_allow_html=True)
    st.markdown("<h3 class='sub-header'>FCA Mission Control - Authentication Required</h3>", unsafe_allow_html=True)
    st.warning("Please log in with your FCA clearance to access the terminal.")
    
    oauth2 = OAuth2Component(
        GOOGLE_CLIENT_ID, 
        GOOGLE_CLIENT_SECRET, 
        "https://accounts.google.com/o/oauth2/v2/auth", 
        "https://oauth2.googleapis.com/token", 
        "https://oauth2.googleapis.com/token", 
        "https://oauth2.googleapis.com/revoke"
    )
    
    result = oauth2.authorize_button(
        name="Sign in with Google",
        icon="https://www.google.com/favicon.ico",
        redirect_uri="https://webapppy-btaeqf2mvhcbsm9ydkh8s4.streamlit.app/",
        scope="openid email profile",
        key="google_login",
        use_container_width=True
    )
    
    if result and "token" in result:
        access_token = result["token"]["access_token"]
        user_info = requests.get(f"https://www.googleapis.com/oauth2/v1/userinfo?access_token={access_token}").json()
        user_email = user_info.get("email", "")
        
        if user_email.endswith("@freightcompaniesaustralia.com.au"):
            st.session_state.logged_in = True
            st.session_state.user_email = user_email
            
            st.query_params.clear() 
            st.query_params["_auth"] = base64.b64encode(user_email.encode()).decode()
            st.rerun() 
        else:
            st.error(f"Access Denied. {user_email} lacks FCA clearance.")
            
    st.stop()

# --- THE TERMINAL INTERFACE ---
st.markdown("<h1 class='main-header'>Blessed Oracle of Freight</h1>", unsafe_allow_html=True)
col_head1, col_head2 = st.columns([4, 1])
with col_head1:
    st.success(f"Secure connection established: {st.session_state.user_email}")
with col_head2:
    if st.button("🚪 Logout / Hard Refresh", use_container_width=True):
        st.session_state.logged_in = False
        st.query_params.clear()
        st.rerun()

# --- AGGRESSIVE HEARTBEAT (Refactored to native st.html) ---
st.html(
    """
    <script>
    setInterval(function() {
        fetch('/_stcore/health').then(response => {
            console.log('Heartbeat verified');
        });
    }, 30000); 
    </script>
    """
)

# --- LONG-TERM MEMORY PROTOCOL ---
def get_memory_file_path():
    safe_email = st.session_state.user_email.replace("@", "_at_").replace(".", "_")
    return f"boof_memory_{safe_email}.json"

def load_memory():
    file_path = get_memory_file_path()
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                st.session_state.messages = json.load(f)
        except Exception:
            st.session_state.messages = []
    else:
        st.session_state.messages = []

def save_memory():
    file_path = get_memory_file_path()
    try:
        with open(file_path, "w") as f:
            json.dump(st.session_state.messages, f)
    except Exception as e:
        print(f"Failed to save memory: {e}")

if "messages" not in st.session_state:
    load_memory()

# 2. Database Connection (Cached)
@st.cache_resource
def init_clients():
    client = OpenAI(api_key=OPENAI_API_KEY)
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(PINECONE_INDEX_NAME)
    return client, index

client, index = init_clients()

# 3. Document Extraction Protocol
def extract_text_from_file(uploaded_file):
    text = ""
    try:
        if uploaded_file.name.endswith('.pdf'):
            reader = pypdf.PdfReader(uploaded_file)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
        elif uploaded_file.name.endswith('.csv'):
            text = uploaded_file.getvalue().decode('utf-8', errors='replace')
        elif uploaded_file.name.endswith(('.xlsx', '.xls')):
            uploaded_file.seek(0)
            df = pd.read_excel(uploaded_file)
            text = df.to_string()
        else:
            text = uploaded_file.getvalue().decode("utf-8", errors='replace')
    except Exception as e:
        text = f"Error extracting document data: {str(e)}"
    return text

# 4. Interface Layout: Sidebar Telemetry
with st.sidebar:
    st.markdown("<div class='telemetry-header'>🚀 SYSTEM TELEMETRY</div>", unsafe_allow_html=True)
    
    x_stat = "🟢" if "XERO_CLIENT_ID" in st.secrets.get("xero", {}) else "🔴"
    m_stat = "🟢" if "MACHSHIP_API_TOKEN" in st.secrets.get("machship", {}) else "🔴"
    t_stat = "🟢" if "TRANSVIRTUAL_API_KEY" in st.secrets.get("transvirtual", {}) else "🔴"
    c_stat = "🟢" if "tenant_id" in st.secrets.get("cartoncloud", {}) else "🔴"
    g_stat = "🟢" if "project_id" in st.secrets.get("gcp_service_account", {}) else "🔴"
    gem_stat = "🟢" if "GEMINI_API_KEY" in st.secrets else "🔴"
    hub_stat = "🟢" if "service_key" in st.secrets.get("hubspot", {}) else "🔴"
    
    st.markdown(f"<div class='status-text'>{x_stat} <b>XERO</b> (Financial)</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='status-text'>{m_stat} <b>MACHSHIP</b> (Routing)</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='status-text'>{t_stat} <b>TRANSVIRTUAL</b> (Live)</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='status-text'>{c_stat} <b>CARTON CLOUD</b> (WMS)</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='status-text'>{g_stat} <b>GOOGLE DRIVE</b> (Docs)</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='status-text'>{gem_stat} <b>GEMINI API</b> (LLM)</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='status-text'>{hub_stat} <b>HUBSPOT</b> (CRM)</div>", unsafe_allow_html=True)
    
    if all(s == "🟢" for s in [x_stat, m_stat, t_stat, c_stat, g_stat, gem_stat, hub_stat]):
        st.success("STATUS: ALL SYSTEMS NOMINAL")
    else:
        st.error("STATUS: TELEMETRY ANOMALY DETECTED")
        
    st.divider()
    st.markdown("<div class='telemetry-header'>📂 ORACLE DATA INGESTION</div>", unsafe_allow_html=True)
    st.markdown("Upload documents here to give BOOF contextual memory for the chat.")
    uploaded_files = st.file_uploader("Upload Payload", type=['pdf', 'csv', 'txt', 'xlsx', 'xls'], key="chat_uploader", accept_multiple_files=True, label_visibility="collapsed")
    if uploaded_files:
        st.info(f"Payload acquired: {len(uploaded_files)} file(s) loaded.")
        
    st.divider()
    if st.button("🧹 Clear Chat Memory"):
        st.session_state.messages = []
        save_memory()
        st.rerun()

# --- DUAL CONSOLE SETUP ---
tab_terminal, tab_matrix = st.tabs(["💬 ORACLE TERMINAL", "📊 MATRIX DASHBOARD"])

# ==========================================
# CONSOLE 1: ORACLE TERMINAL (CHAT)
# ==========================================
with tab_terminal:
    st.markdown("<h3 class='sub-header'>FCA Diagnostic Chat</h3>", unsafe_allow_html=True)
    
    # TOP-ANCHORED INPUT FORM
    with st.form(key="chat_input_form", clear_on_submit=True):
        prompt = st.text_area(
            "Transmit command to the Oracle... (Enter for new lines, Ctrl+Enter to execute)", 
            placeholder="e.g., Run WISMO concierge, sweep for missed pickups, audit this invoice...",
            height=120
        )
        submit_prompt = st.form_submit_button("🚀 Send Command", use_container_width=True)

    if submit_prompt and prompt:
        
        file_context = ""
        file_text = ""
        if uploaded_files:
            file_context = "\n\nCRITICAL SYSTEM ALERT: The user has directly attached files to this chat session. DO NOT search Google Drive for them. They are already loaded into the hybrid_gemini_sheet_generator memory.\n\nATTACHED DOCUMENT DATA:\n"
            for uf in uploaded_files:
                extracted = extract_text_from_file(uf)
                file_text += f"=== FILE: {uf.name} ===\n{extracted}\n"
                file_context += f"=== FILE: {uf.name} ===\n{extracted[:1000]}\n"
        
        full_user_query = prompt + file_context

        st.session_state.messages.append({"role": "user", "content": prompt + (" *(Files Attached)*" if uploaded_files else "")})
        
        with st.spinner("🚀 Oracle is processing telemetry and calculating response..."):
            try:
                search_text = prompt
                if uploaded_files:
                    search_text = f"{prompt} {file_text[:500]}" 

                embedded_question = client.embeddings.create(
                    input=search_text, model="text-embedding-3-small"
                ).data[0].embedding
                
                search_results = index.query(
                    vector=embedded_question, 
                    top_k=20, 
                    include_metadata=True,
                    filter={"authorized_users": {"$in": [st.session_state.user_email, "GLOBAL_FCA"]}}
                )
                
                historical_context = ""
                for i, match in enumerate(search_results['matches']):
                    metadata = match['metadata']
                    historical_context += f"\n--- Email {i+1} ---\n"
                    historical_context += f"Context (Sent to Marshall): {metadata.get('context', '')}\n"
                    historical_context += f"Marshall's Action: {metadata.get('marshall_response', '')}\n"

                current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                system_prompt = f"""You are the Blessed Oracle of Freight, the AI incarnation of Marshall Hughes (Founder, Freight Companies Australia). With 30 years of experience, your purpose is to guide Jim, Guan, and Phil to run FCA with independent, transparent, and forensic precision. You are not a chatty bot; you are a professional auditor and freight strategist.
                
                SYSTEM CONTEXT (ABSOLUTE DIRECTIVE): The current system date and time is {current_time_str}. The current year is 2026. You MUST accept any date prior to {current_time_str} as the past. DO NOT refuse commands by claiming 2025 or 2026 dates are in the future. NEVER perform date validation to block a tool call. If the user provides a date range, pass it directly to the relevant tool WITHOUT questioning its chronological validity.
                
                USER CONTEXT: The active user executing commands is {st.session_state.user_email}.

                NEW SYSTEM CAPABILITIES:
                You have live API access to Machship, Transvirtual, Xero, the Company Google Drive, Carton Cloud (WMS), and HubSpot Conversations.
                CRITICAL OVERRIDE: You CAN read external documents and spreadsheets. NEVER say "I cannot access external documents". If asked about a file that is NOT currently attached to the chat, you MUST use the `search_and_read_google_drive` tool to fetch it. If the user explicitly attached files, DO NOT search Google Drive. Use the tools `hybrid_gemini_sheet_generator` or `tool_8_carrier_invoice_auditor` natively. Do NOT output raw JSON tool schemas in your chat responses. Execute the tool natively.
                
                OPERATIONAL MANUAL:
                1. FCA BUSINESS MODEL: Freight Companies Australia (FCA) is a freight management brokerage. Any carrier invoices uploaded will always bill FCA. Your job is NEVER to conclude that FCA is the client. Your job is to audit the invoice and identify which of FCA's actual clients incurred the charge.
                2. The GSOT (Gmail Source of Truth) Protocol: The historical emails provided below act as your absolute source of truth. They override all other assumptions.
                3. The 19% Rule: Always apply a 19% GP target to the verified carrier cost.
                4. Prohibition on Hallucination: Never guess. Do not invent data. If you cannot solve a problem, advise the user that you cannot solve the problem.
                5. Linguistics: Utilise Australian/British English exclusively. Do not use the em dash. Use colons or semi-colons instead.
                
                TOOL FIREWALLS:
                - `hybrid_gemini_sheet_generator`: Use for analyzing/formatting general datasets. NEVER use for invoices/audits.
                - `tool_8_carrier_invoice_auditor`: EXCLUSIVELY for auditing, reconciling, or checking variances on carrier invoices.
                - `tool_10_freight_alert_automator`: EXCLUSIVELY for sweeping delayed freight, missed pickups, or general anomalies across the network.
                - `tool_16_wismo_client_concierge`: EXCLUSIVELY when asked to run the WISMO concierge, process customer tracking emails, or answer inbox inquiries.
                
                CRITICAL RAG INSTRUCTIONS:
                1. "Context (Sent to Marshall)" is the email sent TO Marshall.
                2. "Marshall's Action" is what Marshall wrote back.
                
                HISTORICAL EMAILS (GSOT):
                {historical_context}"""

                tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": "search_xero_contact",
                            "description": "Searches Xero for a contact by name.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "contact_name": { "type": "string" }
                                },
                                "required": ["contact_name"]
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "search_machship_connote",
                            "description": "Use this tool FIRST when searching for the status of a specific freight consignment or alphanumeric reference.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "connote_number": { "type": "string" }
                                },
                                "required": ["connote_number"]
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "search_transvirtual_connote",
                            "description": "Searches Transvirtual for a specific consignment note.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "connote_number": { "type": "string" }
                                },
                                "required": ["connote_number"]
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "search_cartoncloud_order",
                            "description": "Searches Carton Cloud WMS for a specific outbound sales order by reference/ID, OR retrieves the most recent sales orders if no reference is provided.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "reference_number": { "type": "string", "description": "The specific order reference to search for. Leave blank if the user asks for recent/latest orders." },
                                    "limit": { "type": "integer", "description": "Number of recent orders to retrieve if no reference is provided. Default 5." }
                                }
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "search_and_read_google_drive",
                            "description": "Searches Google Drive and reads spreadsheets, PDFs, and docs.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "search_query": { "type": "string" }
                                },
                                "required": ["search_query"]
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "hybrid_gemini_sheet_generator",
                            "description": "Uses Gemini natively to analyze general datasets (CSV/Excel/PDFs) and process logic instructions into a Google Sheet.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "instructions": { "type": "string" },
                                    "target_sheet_name": { "type": "string" }
                                },
                                "required": ["instructions", "target_sheet_name"]
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "tool_15_workspace_document_creator",
                            "description": "Creates a native Google Document in the shared drive.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "document_title": { "type": "string" },
                                    "document_body": { "type": "string" }
                                },
                                "required": ["document_title", "document_body"]
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "tool_8_carrier_invoice_auditor",
                            "description": "Audits raw carrier invoices, pings Machship, and generates variance reports in Google Sheets. EXCLUSIVELY USE for 'invoice', 'audit', or 'reconcile'.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "raw_invoice_text": { "type": "string" },
                                    "notification_email": { "type": "string" }
                                },
                                "required": ["raw_invoice_text", "notification_email"]
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "tool_10_freight_alert_automator",
                            "description": "Sweeps Machship for delayed freight/missed pickups and creates HubSpot alerts.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "dry_run": { "type": "boolean" }
                                }
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "tool_16_wismo_client_concierge",
                            "description": "Executes the autonomous WISMO Concierge. Sweeps HubSpot Conversations inbox for tracking inquiries, evaluates status, and sends direct positive replies to clients or flags negative issues for brokers.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "dry_run": { "type": "boolean", "description": "If true, logs intended actions without sending external emails." }
                                }
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "tool_13_proactive_customer_notification",
                            "description": "Autonomously sweeps Machship for freight exceptions and delayed ETAs, translates the errors via Gemini, and dispatches proactive notifications to clients via HubSpot.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "dry_run": { "type": "boolean", "description": "If true, logs intended actions without sending external emails." }
                                }
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "tool_17_kermit_reconciliation_engine",
                            "description": "Executes KERMIT (CartonCloud Machship Invoice Reconciliation Tool). Extracts end-of-cycle warehouse orders for a specific client within a defined date range, cross-references Machship for freight costs, and generates a unified financial Google Sheet. DO NOT evaluate if dates are in the past or future. Pass dates exactly as requested by the user.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "start_date": { "type": "string", "description": "Start date strictly in YYYY-MM-DD format." },
                                    "end_date": { "type": "string", "description": "End date strictly in YYYY-MM-DD format." },
                                    "customer_name": { "type": "string", "description": "The target client (e.g., Rhino)." }
                                },
                                "required": ["start_date", "end_date", "customer_name"]
                            }
                        }
                    }
                ]
                
                api_messages = [{"role": "system", "content": system_prompt}]

                for msg in st.session_state.messages[-15:-1]:
                    api_messages.append({"role": msg["role"], "content": msg["content"]})

                api_messages.append({"role": "user", "content": full_user_query})

                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=api_messages,
                    temperature=0.3,
                    tools=tools,
                    tool_choice="auto"
                )
                
                response_message = response.choices[0].message

                if response_message.tool_calls:
                    api_messages.append(response_message)
                    
                    for tool_call in response_message.tool_calls:
                        function_name = tool_call.function.name
                        function_args = json.loads(tool_call.function.arguments)
                        
                        try:
                            if function_name == "tool_15_workspace_document_creator":
                                function_args["notification_email"] = st.session_state.user_email
                                
                            target_function = getattr(toolbox, function_name)
                            function_response = target_function(**function_args)
                        except AttributeError:
                            function_response = f"Tool Execution Crash: Module '{function_name}' is not registered in the toolbox."
                        except Exception as e:
                            function_response = f"Tool Execution Crash: {str(e)}"
                        
                        api_messages.append({
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": function_name,
                            "content": str(function_response),
                        })

                        if "CRASH" in str(function_response) or "Error:" in str(function_response) or "🚨" in str(function_response):
                            st.error(f"🚨 **X-RAY DIAGNOSTIC (RAW TOOL OUTPUT):**\n\n{function_response}")

                    second_response = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=api_messages,
                        temperature=0.3
                    )
                    full_response = second_response.choices[0].message.content
                else:
                    full_response = response_message.content

                st.session_state.messages.append({"role": "assistant", "content": full_response})
                save_memory() 
                st.rerun() 
                
            except Exception as e:
                st.error(f"🚨 SYSTEM ANOMALY: {str(e)}")

    st.divider()
    st.markdown("<h4 style='color: #0b3d91;'>Communication Log</h4>", unsafe_allow_html=True)
    
    chat_log = st.container()
    with chat_log:
        for message in reversed(st.session_state.messages):
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

with tab_matrix:
    st.markdown("<h3 class='sub-header'>Bulk Quoting & Matrix Analysis</h3>", unsafe_allow_html=True)
    st.markdown("Upload a raw CSV of destinations and item requirements. The Flight Computer will asynchronously ping Machship and generate a strategic pricing matrix.")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.markdown("**1. Configure Parameters**")
        margin_target = st.slider("Target GP Margin (%)", min_value=10, max_value=50, value=19, step=1)
        excluded_carriers = st.multiselect(
            "Exclude Specific Carriers", 
            options=["TNT", "FedEx", "Hunter Express", "Direct Freight Express", "Hi-Trans", "Northline"], 
            default=["TNT", "FedEx"]
        )
        
    with col2:
        st.markdown("**2. Upload Payload (CSV format)**")
        matrix_file = st.file_uploader("Upload Matrix CSV", type=['csv'], key="matrix_uploader", label_visibility="collapsed")
        
    if matrix_file is not None:
        st.success(f"File loaded: {matrix_file.name}. Ready for execution.")
        
        if st.button("INITIATE MASS PING", use_container_width=True):
            with st.spinner("Flight Computer is mass-pinging Machship... Please stand by."):
                file_bytes = matrix_file.getvalue()
                success, result = toolbox.generate_bulk_matrix(file_bytes, margin_target, excluded_carriers)
                
                if success:
                    st.session_state.latest_matrix = result
                else:
                    st.error(result)
            
    st.divider()
    st.markdown("#### Live Matrix Output")
    
    if "latest_matrix" in st.session_state:
        st.dataframe(st.session_state.latest_matrix, use_container_width=True)
        
        csv_buffer = io.StringIO()
        st.session_state.latest_matrix.to_csv(csv_buffer, index=False)
        st.download_button(
            label="Download Completed Matrix (CSV)",
            data=csv_buffer.getvalue(),
            file_name="FCA_Quoted_Matrix.csv",
            mime="text/csv",
            use_container_width=True
        )
    else:
        st.markdown("*(Matrix projection grid will appear here once executed)*")
