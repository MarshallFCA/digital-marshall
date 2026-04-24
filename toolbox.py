import requests
import base64
import re
import json
import streamlit as st

# ==========================================
# XERO CONNECTION HANDSHAKE
# ==========================================
def get_xero_token():
    client_id = st.secrets["XERO_CLIENT_ID"]
    client_secret = st.secrets["XERO_CLIENT_SECRET"]
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    url = "https://identity.xero.com/connect/token"
    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = { "grant_type": "client_credentials" }
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        return f"Error connecting to Xero: {response.text}"

# ==========================================
# TOOL 1: XERO FINANCIAL SEARCH
# ==========================================
def search_xero_contact(contact_name):
    token = get_xero_token()
    if "Error" in token: return token 
    url = f'https://api.xero.com/api.xro/2.0/Contacts?where=Name.Contains("{contact_name}")'
    headers = { "Authorization": f"Bearer {token}", "Accept": "application/json" }
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        if data.get("Contacts"):
            contact = data["Contacts"][0]
            name = contact.get("Name", "Unknown")
            status = contact.get("ContactStatus", "Unknown")
            
            # Dig into Xero's financial balances
            balances = contact.get("Balances", {}).get("AccountsReceivable", {})
            outstanding = balances.get("Outstanding", 0.00)
            overdue = balances.get("Overdue", 0.00)
            
            # Format the raw dictionary for the AI
            raw_data = json.dumps(contact, indent=2)
            
            return f"✅ Xero Record: {name} | Status: {status} | Total Outstanding: ${outstanding} | Overdue: ${overdue}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"
        else:
            return f"No contact found in Xero matching '{contact_name}'."
    else:
        return f"Xero API Error: {response.text}"

# ==========================================
# TOOL 2: UNRESTRICTED MACHSHIP SEARCH
# ==========================================
def search_machship_connote(connote_number):
    token = st.secrets["MACHSHIP_API_TOKEN"]
    connote_number = connote_number.strip().upper()
    headers = { "token": token, "Accept": "application/json" }

    # PATH A: It is an internal Machship number
    if connote_number.startswith("MS"):
        ms_id = re.sub(r"\D", "", connote_number)
        url = f"https://live.machship.com/apiv2/consignments/getConsignment?id={ms_id}"
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("object"):
                consignment = data["object"]
                carrier = consignment.get("carrier", {}).get("name") or consignment.get("carrier", {}).get("abbreviation") or "Carrier Not Assigned"
                status = consignment.get("status", {}).get("name", "Unknown Status")
                
                raw_data = json.dumps(consignment, indent=2)
                return f"✅ Machship Record (MS): Carrier: {carrier} | Status: {status}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"
            else:
                return f"Could not find MS consignment '{connote_number}'."
        else:
            return f"API Error (MS Search): {response.text}"

    # PATH B: Carrier ID & Reference Hunt
    headers["Content-Type"] = "application/json"
    
    search_routes = [
        ("Carrier ID", "https://live.machship.com/apiv2/consignments/returnConsignmentsByCarrierConsignmentId?includeChildCompanies=true"),
        ("Reference 1", "https://live.machship.com/apiv2/consignments/returnConsignmentsByReference1?includeChildCompanies=true"),
        ("Reference 2", "https://live.machship.com/apiv2/consignments/returnConsignmentsByReference2?includeChildCompanies=true")
    ]

    payload = [connote_number]

    for search_type, url in search_routes:
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("object") and len(data["object"]) > 0:
                consignment = data["object"][0]
                carrier = consignment.get("carrier", {}).get("name") or consignment.get("carrier", {}).get("abbreviation") or "Carrier Not Assigned"
                status = consignment.get("status", {}).get("name", "Unknown Status")
                
                raw_data = json.dumps(consignment, indent=2)
                return f"✅ Machship Record (Found via {search_type}): Carrier: {carrier} | Status: {status}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"

    return f"Failed to find '{connote_number}' in Machship."
# ==========================================
# TOOL 3: TRANSVIRTUAL CONSIGNMENT SEARCH
# ==========================================
def search_transvirtual_connote(connote_number):
    import json
    import requests
    import streamlit as st
    
    token = st.secrets["TRANSVIRTUAL_API_KEY"]
    connote_number = connote_number.strip().upper()
    
    headers = {
        "Authorization": token, 
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    try:
        # 1. Authenticate using Streamlit Secrets
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        service = build('drive', 'v3', credentials=creds)

        # 2. Search for the file (searches titles and full text)
        query = f"fullText contains '{search_query}' or name contains '{search_query}'"
        results = service.files().list(
            q=query,
            pageSize=3,
            fields="nextPageToken, files(id, name, mimeType)"
        ).execute()
        
        items = results.get('files', [])
        
        if not items:
            return f"No documents found in Google Drive matching: '{search_query}'. Ensure the file is shared with the Service Account email."
            
        # 3. Read the first (most relevant) document
        file = items[0]
        file_id = file['id']
        file_name = file['name']
        mime_type = file['mimeType']
        
        content = ""
        
        # Extract Google Doc
        if 'application/vnd.google-apps.document' in mime_type:
            request = service.files().export_media(fileId=file_id, mimeType='text/plain')
            content = request.execute().decode('utf-8')
            
        # Extract Google Sheet (Converted to CSV for the AI)
        elif 'application/vnd.google-apps.spreadsheet' in mime_type:
            request = service.files().export_media(fileId=file_id, mimeType='text/csv')
            content = request.execute().decode('utf-8')
            
        # Extract Excel File (.xlsx)
        elif 'spreadsheetml.sheet' in mime_type or 'application/vnd.ms-excel' in mime_type:
            import pandas as pd
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO(request.execute())
            df = pd.read_excel(fh)
            content = df.to_csv(index=False)
            
        # Extract PDF
        elif 'application/pdf' in mime_type:
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            
            fh.seek(0)
            pdf_reader = PyPDF2.PdfReader(fh)
            for page in pdf_reader.pages:
                if page.extract_text():
                    content += page.extract_text() + "\n"
                
        # Extract Plain Text File
        elif 'text/plain' in mime_type or 'text/csv' in mime_type:
            request = service.files().get_media(fileId=file_id)
            content = request.execute().decode('utf-8')
            
        else:
            return f"Found '{file_name}', but it is a format ({mime_type}) that Digital Marsh cannot read yet."

        # Truncate content to avoid blowing out the AI's context window memory
        max_chars = 15000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... [TRUNCATED DUE TO LENGTH: Data exceeds AI memory limit. Summarizing the first 15,000 characters.]"

        return f"✅ GOOGLE DRIVE MATCH FOUND: '{file_name}'\n\n**Document Content:**\n{content}"

    except Exception as e:
        return f"🚨 Google Drive Connection Crash: {str(e)}"
# ==========================================
# TOOL 4: GOOGLE DRIVE ORACLE
# ==========================================
def search_and_read_google_drive(search_query):
    import streamlit as st
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    import io
    import PyPDF2

    try:
        # 1. Authenticate using Streamlit Secrets
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        service = build('drive', 'v3', credentials=creds)

        # 2. Search for the file (searches titles and full text)
        query = f"fullText contains '{search_query}' or name contains '{search_query}'"
        results = service.files().list(
            q=query,
            pageSize=3, # Grabs the top 3 matches
            fields="nextPageToken, files(id, name, mimeType)"
        ).execute()
        
        items = results.get('files', [])
        
        if not items:
            return f"No documents found in Google Drive matching: '{search_query}'. Ensure the file is shared with the Service Account email."
            
        # 3. Read the first (most relevant) document
        file = items[0]
        file_id = file['id']
        file_name = file['name']
        mime_type = file['mimeType']
        
        content = ""
        
        # Extract Google Doc
        if 'application/vnd.google-apps.document' in mime_type:
            request = service.files().export_media(fileId=file_id, mimeType='text/plain')
            content = request.execute().decode('utf-8')
            
        # Extract PDF
        elif 'application/pdf' in mime_type:
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            
            fh.seek(0)
            pdf_reader = PyPDF2.PdfReader(fh)
            for page in pdf_reader.pages:
                if page.extract_text():
                    content += page.extract_text() + "\n"
                
        # Extract Plain Text File
        elif 'text/plain' in mime_type:
            request = service.files().get_media(fileId=file_id)
            content = request.execute().decode('utf-8')
            
        else:
            return f"Found '{file_name}', but it is a format ({mime_type}) that Digital Marsh cannot read yet. Please use Google Docs, PDFs, or TXT files."

        # Truncate content to avoid blowing out the AI's context window memory
        max_chars = 15000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... [TRUNCATED DUE TO LENGTH]"

        return f"✅ GOOGLE DRIVE MATCH FOUND: '{file_name}'\n\n**Document Content:**\n{content}"

    except Exception as e:
        return f"🚨 Google Drive Connection Crash: {str(e)}"
