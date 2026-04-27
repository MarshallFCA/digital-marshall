import streamlit as st
from openai import OpenAI
from pinecone import Pinecone
import PyPDF2
import pandas as pd
import io
import json
import requests
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
    /* Import Poppins from Google Fonts */
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700;900&display=swap');

    /* Globally apply Poppins to all standard text elements */
    .stApp, p, h1, h2, h3, h4, h5, h6, li, label, input, button, .stMarkdown, div[data-testid="stText"] {
        font-family: 'Poppins', sans-serif !important;
    }

    .stApp {
        background-color: #ffffff;
    }
    .main-header {
        color: #0b3d91; /* NASA Blue */
        font-weight: 900;
        text-transform: uppercase;
        letter-spacing: 2px;
    }
    .sub-header {
        color: #fc3d21; /* NASA Red */
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
    
    /* Style the Tabs to look like control panels */
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
    </style>
""", unsafe_allow_html=True)

# --- GOOGLE SSO BOUNCER ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

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
            st.rerun() 
        else:
            st.error(f"Access Denied. {user_email} lacks FCA clearance.")
            
    st.stop()

# --- THE TERMINAL INTERFACE ---
st.markdown("<h1 class='main-header'>Blessed Oracle of Freight</h1>", unsafe_allow_html=True)
st.success(f"Secure connection established: {st.session_state.user_email}")

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
            reader = PyPDF2.PdfReader(uploaded_file)
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
    
    # Ping Secrets to determine status
    x_stat = "🟢" if "XERO_CLIENT_ID" in st.secrets.get("xero", {}) else "🔴"
    m_stat = "🟢" if "MACHSHIP_API_TOKEN" in st.secrets.get("machship", {}) else "🔴"
    t_stat = "🟢" if "TRANSVIRTUAL_API_KEY" in st.secrets.get("transvirtual", {}) else "🔴"
    c_stat = "🟢" if "tenant_id" in st.secrets.get("cartoncloud", {}) else "🔴"
    g_stat = "🟢" if "project_id" in st.secrets.get("gcp_service_account", {}) else "🔴"
    
    st.markdown(f"<div class='status-text'>{x_stat} <b>XERO</b> (Financial)</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='status-text'>{m_stat} <b>MACHSHIP</b> (Routing)</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='status-text'>{t_stat} <b>TRANSVIRTUAL</b> (Live)</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='status-text'>{c_stat} <b>CARTON CLOUD</b> (WMS)</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='status-text'>{g_stat} <b>GOOGLE DRIVE</b> (Docs)</div>", unsafe_allow_html=True)
    
    if all(s == "🟢" for s in [x_stat, m_stat, t_stat, c_stat, g_stat]):
        st.success("STATUS: ALL SYSTEMS NOMINAL")
    else:
        st.error("STATUS: TELEMETRY ANOMALY DETECTED")
        
    st.divider()
    st.markdown("<div class='telemetry-header'>📂 ORACLE DATA INGESTION</div>", unsafe_allow_html=True)
    st.markdown("Upload documents here to give BOOF contextual memory for the chat.")
    uploaded_files = st.file_uploader("", type=['pdf', 'csv', 'txt', 'xlsx', 'xls'], key="chat_uploader", accept_multiple_files=True)
    if uploaded_files:
        st.info(f"Payload acquired: {len(uploaded_files)} file(s) loaded.")

# --- DUAL CONSOLE SETUP ---
tab_terminal, tab_matrix = st.tabs(["💬 ORACLE TERMINAL", "📊 MATRIX DASHBOARD"])

# ==========================================
# CONSOLE 1: ORACLE TERMINAL (CHAT)
# ==========================================
with tab_terminal:
    st.markdown("<h3 class='sub-header'>FCA Diagnostic Chat</h3>", unsafe_allow_html=True)
    
    if "messages" not in st.session_state:
        st.session_state.messages = []

    chat_log = st.container()

    with chat_log:
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    if prompt := st.chat_input("Transmit command to the Oracle..."):
        
        file_context = ""
        file_text = ""
        if uploaded_files:
            file_context = "\n\nCRITICAL SYSTEM ALERT: The user has directly attached files to this chat session. DO NOT search Google Drive for them. They are already loaded into the hybrid_gemini_sheet_generator memory.\n\nATTACHED DOCUMENT DATA:\n"
            for uf in uploaded_files:
                extracted = extract_text_from_file(uf)
                file_text += f"=== FILE: {uf.name} ===\n{extracted}\n"
                # Give OpenAI a taste of each file so it knows what's inside
                file_context += f"=== FILE: {uf.name} ===\n{extracted[:1000]}\n"
        
        full_user_query = prompt + file_context

        st.session_state.messages.append({"role": "user", "content": prompt + (" *(Files Attached)*" if uploaded_files else "")})
        
        with chat_log:
            with st.chat_message("user"):
                st.markdown(prompt + (" *(Files Attached)*" if uploaded_files else ""))

            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                
                try:
                    # A. Vector Conversion
                    search_text = prompt
                    if uploaded_files:
                        search_text = f"{prompt} {file_text[:500]}" 

                    embedded_question = client.embeddings.create(
                        input=search_text, model="text-embedding-3-small"
                    ).data[0].embedding
                    
                    # B. GSOT Retrieval
                    search_results = index.query(
                        vector=embedded_question, top_k=20, include_metadata=True
                    )
                    
                    historical_context = ""
                    for i, match in enumerate(search_results['matches']):
                        metadata = match['metadata']
                        historical_context += f"\n--- Email {i+1} ---\n"
                        historical_context += f"Context (Sent to Marshall): {metadata.get('context', '')}\n"
                        historical_context += f"Marshall's Action: {metadata.get('marshall_response', '')}\n"

                    # C. Logic Engine Execution
                    system_prompt = f"""You are the Blessed Oracle of Freight, the AI incarnation of Marshall Hughes (Founder, Freight Companies Australia). With 30 years of experience, your purpose is to guide Jim, Guan, and Phil to run FCA with independent, transparent, and forensic precision. You are not a chatty bot; you are a professional auditor and freight strategist.

                    NEW SYSTEM CAPABILITIES:
                    You have live API access to Machship, Transvirtual, Xero, the Company Google Drive, and Carton Cloud (WMS). Use Carton Cloud to check warehouse order statuses and dispatch details. 
                    CRITICAL OVERRIDE: You CAN read external documents and spreadsheets. NEVER say "I cannot access external documents". If asked about a file that is NOT currently attached to the chat, you MUST use the `search_and_read_google_drive` tool to fetch it. If the user explicitly attached files, DO NOT search Google Drive. Use the `hybrid_gemini_sheet_generator` tool instead. Do NOT output raw JSON tool schemas in your chat responses. Execute the tool natively.
                    OPERATIONAL MANUAL:
                    1. FCA BUSINESS MODEL (CRITICAL): Freight Companies Australia (FCA) is a freight management brokerage. Any carrier invoices uploaded (e.g., from Tranzworks, FedEx, Northline) will always bill FCA. Your job is NEVER to conclude that FCA is the client. Your job is to audit the invoice and identify which of FCA's actual clients (e.g., Henselite, ASGA, BOA, AC Solar) incurred the charge based on the "Reference", "Caller", "Job Details", or pickup/delivery locations, so FCA can on-charge them.
                    2. The GSOT (Gmail Source of Truth) Protocol: The historical emails provided below act as your absolute source of truth. They override all other assumptions.
                    3. The "Handshake" Rule: Any carrier commitment found in these emails overrides standard carrier terms.
                    4. Conflict Resolution: If external data conflicts with the GSOT, the GSOT wins. Flag as "Overcharge Alert".
                    5. BOA Protocol: BOA has no TMS data. Use historical quotes. If no record exists, apply a 17% GP rule.
                    6. The "Big 5" Client Rules:
                       * BOA: No Machship. Use historical quotes. Apply 17% GP rule.
                       * CALM: Scenario A (Freight/Benchmarking) = Client. Scenario B (Warehousing/Pick-Pack) = Supplier. Do not confuse.
                       * AC Solar: Watch for overlength (>2.4m). If no forklift, tailgate is mandatory.
                       * ACRRM: Medical freight. Tier 1 tracking (FedEx/TGE) only.
                       * Regroup: Industrial pallets. Focus on linehaul efficiency and pre-calls.
                    7. Carrier Selection & "The Shield":
                       * Heavy/Pallets: Northline, Hi-Trans, Direct Freight.
                       * Satchels/Parcels: FedEx (TNT), Team Global Express (TGE).
                       * The Shield: Always query Tailgate, Manual Handling, and Residential surcharges without a quote flag.
                    8. Operational Logic & Tone:
                       * Tone: Independent, professional, firm, transparent. Act as the Star Trek TNG Computer. No chatter.
                       * Output Format: Top of Response: "Forensic Action Plan" or "Recommendation". Body: Analysis, reasoning, and GSOT verification.
                       * The 17% Rule: Always apply a 17% GP target to the verified carrier cost.
                       * Prohibition on Hallucination: Never guess. Do not invent data. If you cannot solve a problem, advise the user that you cannot solve the problem.
                       * Linguistics: Utilise Australian/British English exclusively. Do not use the em dash.
                    9. THE HUNT PROTOCOL: If a user asks for the status of a reference number (e.g., FCU000071), you must autonomously search Machship, Transvirtual, and Carton Cloud. If the first tool returns no result, DO NOT stop. Execute the next tool. Only report failure if all three databases come up empty.
                    10. HYBRID GEMINI PROTOCOL: If the user asks you to analyze a heavy dataset, cross-reference multiple files, audit a large file, or create a spreadsheet from uploaded CSV/Excel files, DO NOT try to read the files yourself and DO NOT search Google Drive for them. You must immediately execute the `hybrid_gemini_sheet_generator` tool.
                    11. TRANSPARENCY PROTOCOL: If any tool returns an error message or crash report (e.g., "HYBRID GEMINI CRASH:" or "Tool Execution Crash:"), you MUST NOT hide it. You must explicitly output the exact error message to the user in your response so they can diagnose the anomaly.

                    CRITICAL RAG INSTRUCTIONS:
                    1. "Context (Sent to Marshall)" is the email sent TO Marshall.
                    2. "Marshall's Action" is what Marshall wrote back.
                    3. When asked "Who" holds a role, identify the specific name from email signatures. Do not answer with a temporary status.
                    4. If a document is attached in the prompt, analyse its text against the GSOT to deduce the client, carrier, or objective.
                    5. INVOICE PARSING: Rigorously scan the document's tabular data for "Reference", "Ref", "Caller", or "Job Details" to identify the true client.
                    
                    HISTORICAL EMAILS (GSOT):
                    {historical_context}"""

                    tools = [
                        {
                            "type": "function",
                            "function": {
                                "name": "search_xero_contact",
                                "description": "Searches Xero for a contact by name and returns their details and outstanding invoice summary.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "contact_name": {
                                            "type": "string",
                                            "description": "The name of the company or person to search for in Xero."
                                        }
                                    },
                                    "required": ["contact_name"]
                                }
                            }
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "search_machship_connote",
                                "description": "Use this tool FIRST when searching for the status of a freight consignment, tracking number, or alphanumeric reference (e.g., FCU000071, MS12345). Returns booking, routing, and pricing details.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "connote_number": {
                                            "type": "string",
                                            "description": "The Machship consignment number (e.g., MS123456) or alphanumeric reference."
                                        }
                                    },
                                    "required": ["connote_number"]
                                }
                            }
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "search_transvirtual_connote",
                                "description": "Searches Transvirtual for a consignment note and returns the booking data and live tracking scans.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "connote_number": {
                                            "type": "string",
                                            "description": "The Transvirtual consignment number."
                                        }
                                    },
                                    "required": ["connote_number"]
                                }
                            }
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "search_and_read_google_drive",
                                "description": "Searches the company Google Drive and reads the contents of spreadsheets, PDFs, and documents. Use this whenever the user asks about a specific file, spreadsheet, or SOP.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "search_query": {
                                            "type": "string",
                                            "description": "The name of the file to search for (e.g., 'Rhino Freight Spreadsheet')."
                                        }
                                    },
                                    "required": ["search_query"]
                                }
                            }
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "search_cartoncloud_order",
                                "description": "Searches the Carton Cloud Warehouse Management System (WMS) for an outbound order status and contents.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "reference_number": {
                                            "type": "string",
                                            "description": "The customer reference number or sale order number (e.g., 'REF-123')."
                                        }
                                    },
                                    "required": ["reference_number"]
                                }
                            }
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "hybrid_gemini_sheet_generator",
                                "description": "Uses Gemini 1.5 Pro to analyze massive datasets (CSV/Excel) currently uploaded in the system (capable of cross-referencing multiple files), extracts specific information based on instructions, and generates a new Google Sheet with the results.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "instructions": {
                                            "type": "string",
                                            "description": "Specific instructions on what data to extract, analyze, cross-reference, or format from the uploaded file(s)."
                                        },
                                        "target_sheet_name": {
                                            "type": "string",
                                            "description": "The title for the newly generated Google Sheet."
                                        }
                                    },
                                    "required": ["instructions", "target_sheet_name"]
                                }
                            }
                        }
                    ]
                    
                    api_messages = [{"role": "system", "content": system_prompt}]

                    for msg in st.session_state.messages[:-1]:
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

                    # DYNAMIC TOOL EXECUTION (SCALABILITY UPGRADE)
                    if response_message.tool_calls:
                        message_placeholder.markdown("*(Oracle is polling telemetry data...)*")
                        
                        api_messages.append(response_message)
                        
                        for tool_call in response_message.tool_calls:
                            function_name = tool_call.function.name
                            function_args = json.loads(tool_call.function.arguments)
                            
                            try:
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

                            # --- THE X-RAY INTERCEPT ---
                            if "CRASH" in str(function_response) or "Error:" in str(function_response):
                                st.error(f"🚨 **X-RAY DIAGNOSTIC (RAW TOOL OUTPUT):**\n\n{function_response}")

                        second_response = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=api_messages,
                            temperature=0.3
                        )
                        full_response = second_response.choices[0].message.content
                    else:
                        full_response = response_message.content

                    message_placeholder.markdown(full_response)
                    st.session_state.messages.append({"role": "assistant", "content": full_response})
                    
                except Exception as e:
                    message_placeholder.error(f"🚨 SYSTEM ANOMALY: {str(e)}")

# ==========================================
# CONSOLE 2: MATRIX DASHBOARD (BULK QUOTING)
# ==========================================
with tab_matrix:
    st.markdown("<h3 class='sub-header'>Bulk Quoting & Matrix Analysis</h3>", unsafe_allow_html=True)
    st.markdown("Upload a raw CSV of destinations and item requirements. The Flight Computer will asynchronously ping Machship and generate a strategic pricing matrix.")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.markdown("**1. Configure Parameters**")
        margin_target = st.slider("Target GP Margin (%)", min_value=10, max_value=50, value=17, step=1)
        excluded_carriers = st.multiselect(
            "Exclude Specific Carriers", 
            options=["TNT", "FedEx", "Hunter Express", "Direct Freight Express", "Hi-Trans", "Northline"], 
            default=["TNT", "FedEx"]
        )
        
    with col2:
        st.markdown("**2. Upload Payload (CSV format)**")
        matrix_file = st.file_uploader("Upload spreadsheet with delivery suburbs and item dimensions.", type=['csv'], key="matrix_uploader")
        
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
        
        # Create downloadable CSV
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

