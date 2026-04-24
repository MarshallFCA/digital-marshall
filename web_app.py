import streamlit as st
from openai import OpenAI
from pinecone import Pinecone
import PyPDF2
import pandas as pd
import io
from streamlit_oauth import OAuth2Component
import requests
import toolbox

# --- CONFIGURATION (SECURED FOR CLOUD) ---
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
PINECONE_API_KEY = st.secrets["PINECONE_API_KEY"]
PINECONE_INDEX_NAME = "digital-marsh"  # It is safe to leave the index name as text

GOOGLE_CLIENT_ID = st.secrets["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = st.secrets["GOOGLE_CLIENT_SECRET"]


# 1. Interface Initialisation
st.set_page_config(page_title="Digital Marshall", page_icon="🗄️", layout="centered")

# --- GOOGLE SSO BOUNCER ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("Digital Marshall")
    st.warning("Please log in with your FCA account to access the Forensic Data Terminal.")
    
    # Setup the Google OAuth component
    oauth2 = OAuth2Component(
        GOOGLE_CLIENT_ID, 
        GOOGLE_CLIENT_SECRET, 
        "https://accounts.google.com/o/oauth2/v2/auth", 
        "https://oauth2.googleapis.com/token", 
        "https://oauth2.googleapis.com/token", 
        "https://oauth2.googleapis.com/revoke"
    )
    
    # Create the Login Button (Using the URL with the slash!)
    result = oauth2.authorize_button(
        name="Sign in with Google",
        icon="https://www.google.com/favicon.ico",
        redirect_uri="https://webapppy-btaeqf2mvhcbsm9ydkh8s4.streamlit.app/",
        scope="openid email profile",
        key="google_login",
        use_container_width=True
    )
    
    if result and "token" in result:
        # We got a token! Now let's check their email address.
        access_token = result["token"]["access_token"]
        user_info = requests.get(f"https://www.googleapis.com/oauth2/v1/userinfo?access_token={access_token}").json()
        user_email = user_info.get("email", "")
        
        # The Checkpoint: Are they FCA staff?
        if user_email.endswith("@freightcompaniesaustralia.com.au"):
            st.session_state.logged_in = True
            st.session_state.user_email = user_email
            st.query_params.clear()  # <-- MAGIC FIX: Wipes the token to prevent ghost reruns
            st.rerun() # Refresh the page to let them in!
        else:
            st.error(f"Access Denied. {user_email} is not an authorized FCA account.")
            
    # Stop the rest of the app from loading until they pass the check
    st.stop()

# --- IF THEY PASS THE BOUNCER, THEY SEE THIS ---
st.title("Digital Marshall")
st.subheader("FCA Forensic Data Terminal")
st.success(f"Secure session active: {st.session_state.user_email}")

# 2. Database Connection
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
            df = pd.read_csv(uploaded_file)
            text = df.to_string()
        elif uploaded_file.name.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(uploaded_file)
            text = df.to_string()
        else:
            text = uploaded_file.getvalue().decode("utf-8")
    except Exception as e:
        text = f"Error extracting document data: {str(e)}"
    return text

# 4. Interface Layout: Sidebar for Uploads
with st.sidebar:
    st.header("Data Ingestion")
    st.markdown("Upload carrier invoices or consignment data for forensic analysis.")
    uploaded_file = st.file_uploader("Upload PDF, CSV, or Excel", type=['pdf', 'csv', 'txt', 'xlsx', 'xls'])

# 5. Session History
if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 6. Query Execution
if prompt := st.chat_input("Input query or command..."):
    
    # Process attachment if present
    file_context = ""
    file_text = ""
    if uploaded_file is not None:
        file_text = extract_text_from_file(uploaded_file)
        file_context = f"\n\nATTACHED DOCUMENT DATA:\n{file_text[:2000]}"
    
    full_user_query = prompt + file_context

    st.session_state.messages.append({"role": "user", "content": prompt + (" (File Attached)" if uploaded_file else "")})
    with st.chat_message("user"):
        st.markdown(prompt + (" *(File Attached)*" if uploaded_file else ""))

    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        
        try:
            # A. Vector Conversion
            search_text = prompt
            if uploaded_file is not None:
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
            import json

            system_prompt = f"""You are Digital Marsh, the AI incarnation of Marshall Hughes (Founder, Freight Companies Australia). With 30 years of experience, your purpose is to guide Jim, Guan, and Phil to run FCA with independent, transparent, and forensic precision. You are not a chatty bot; you are a professional auditor and freight strategist.

            NEW SYSTEM CAPABILITIES:
            You have live API access to Machship, Transvirtual, Xero, and the Company Google Drive. If a user asks about an SOP, contract, rate card, or company policy, ALWAYS use your Google Drive tool to search for and read the document before answering.
            OPERATIONAL MANUAL:
            1. FCA BUSINESS MODEL (CRITICAL): Freight Companies Australia (FCA) is a freight management brokerage. Any carrier invoices uploaded (e.g., from Tranzworks, FedEx, Northline) will always bill FCA. Your job is NEVER to conclude that FCA is the client. Your job is to audit the invoice and identify which of FCA's actual clients (e.g., Henselite, ASGA, BOA, AC Solar) incurred the charge based on the "Reference", "Caller", "Job Details", or pickup/delivery locations, so FCA can on-charge them.
            2. The GSOT (Gmail Source of Truth) Protocol: The historical emails provided below act as your absolute source of truth. They override all other assumptions.
            3. The "Handshake" Rule: Any carrier commitment found in these emails overrides standard carrier terms.
            4. Conflict Resolution: If external data conflicts with the GSOT, the GSOT wins. Flag as "Overcharge Alert".
            5. BOA Protocol: BOA has no TMS data. Use historical quotes. If no record exists, apply a 15% GP rule.
            6. The "Big 5" Client Rules:
               * BOA: No Machship. Use historical quotes. Apply 15% GP rule.
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
               * The 15% Rule: Always apply a 15% GP target to the verified carrier cost.
               * Prohibition on Hallucination: Never guess. Do not invent data. If you cannot solve a problem, advise the user that you cannot solve the problem.
               * Linguistics: Utilise Australian/British English exclusively. Do not use the em dash.

            CRITICAL RAG INSTRUCTIONS:
            1. "Context (Sent to Marshall)" is the email sent TO Marshall.
            2. "Marshall's Action" is what Marshall wrote back.
            3. When asked "Who" holds a role, identify the specific name from email signatures. Do not answer with a temporary status.
            4. If a document is attached in the prompt, analyse its text against the GSOT to deduce the client, carrier, or objective.
            5. INVOICE PARSING: Rigorously scan the document's tabular data for "Reference", "Ref", "Caller", or "Job Details" to identify the true client.
            
            HISTORICAL EMAILS (GSOT):
            {historical_context}"""

            # Define the Tools for OpenAI
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "search_xero_contact",
                        "description": "Searches live Xero for a contact to get their status, total outstanding balance, overdue amounts, and raw financial matrix.",
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
                        "description": "Searches live Machship for a consignment number to get carrier details, routing, status, and raw matrix data.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "connote_number": {
                                    "type": "string",
                                    "description": "The consignment number or carrier reference (e.g., MS64450234, FCAM000006)."
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
                        "description": "Searches live Transvirtual for a consignment number to get sender, receiver, status, and raw tracking data.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "connote_number": {
                                    "type": "string",
                                    "description": "The consignment number or tracking ID used in Transvirtual."
                                }
                            },
                            "required": ["connote_number"]
                        }
                    }
                }
            ]

            # 1. Start the API message list with the System Prompt
            api_messages = [{"role": "system", "content": system_prompt}]

            # 2. Loop through the chat history and add previous messages
            for msg in st.session_state.messages[:-1]:
                api_messages.append({"role": msg["role"], "content": msg["content"]})

            # 3. Add the current query, injecting the hidden file context and RAG data
            api_messages.append({"role": "user", "content": full_user_query})

            # 4. First Call to OpenAI (AI decides if it needs a tool)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=api_messages,
                temperature=0.3,
                tools=tools,
                tool_choice="auto"
            )
            
            response_message = response.choices[0].message

            # 5. Check if the AI wants to use a tool
            if response_message.tool_calls:
                # Tell the chat window what the AI is doing
                message_placeholder.markdown("*(Digital Marsh is auditing live operational data...)*")
                
                # Append the AI's tool request to the conversation history
                api_messages.append(response_message)
                
                # Execute the requested tools
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    function_args = json.loads(tool_call.function.arguments)
                    
                    if function_name == "search_xero_contact":
                        function_response = toolbox.search_xero_contact(function_args.get("contact_name"))
                    elif function_name == "search_machship_connote":
                        function_response = toolbox.search_machship_connote(function_args.get("connote_number"))
                    elif function_name == "search_transvirtual_connote":
                        function_response = toolbox.search_transvirtual_connote(function_args.get("connote_number"))
                    
                    # Hand the raw data matrix back to the AI
                    api_messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": str(function_response),
                    })
                
                # 6. Second Call to OpenAI (AI reads the tool data and writes final answer)
                second_response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=api_messages,
                    temperature=0.3
                )
                full_response = second_response.choices[0].message.content
            else:
                # If no tools were needed, just output the normal text
                full_response = response_message.content

            # 7. Print the final answer to the screen and save to history
            message_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})
            
        except Exception as e:
            message_placeholder.error(f"🚨 SYSTEM ERROR: {str(e)}")
