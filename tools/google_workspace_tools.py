import io
import re
import pandas as pd
import numpy as np
import datetime
import pypdf
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from tools.core_utils import (
    get_secure_endpoint, 
    sanitize_error_log, 
    call_gemini_api, 
    vision_bridge_pdf_to_csv
)

# ==========================================
# TOOL 4: GOOGLE DRIVE ORACLE
# ==========================================
def search_and_read_google_drive(search_query: str) -> str:
    try:
        drive_ro_scope = get_secure_endpoint("google_drive_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kcml2ZS5yZWFkb25seQ==")
        
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=[drive_ro_scope]
        )
        service = build('drive', 'v3', credentials=creds)

        safe_query = search_query.replace("'", "\\'")
        query = f"fullText contains '{safe_query}' or name contains '{safe_query}'"
        
        results = service.files().list(
            q=query,
            pageSize=3,
            orderBy="modifiedTime desc",
            fields="nextPageToken, files(id, name, mimeType)"
        ).execute()
        
        items = results.get('files', [])
        
        if not items:
            return f"No documents found in Google Drive matching: '{search_query}'."
            
        file = items[0]
        file_id = file['id']
        file_name = file['name']
        mime_type = file['mimeType']
        content = ""
        
        if 'application/vnd.google-apps.document' in mime_type:
            request = service.files().export_media(fileId=file_id, mimeType='text/plain')
            content = request.execute().decode('utf-8')
            
        elif 'application/vnd.google-apps.spreadsheet' in mime_type:
            request = service.files().export_media(fileId=file_id, mimeType='text/csv')
            content = request.execute().decode('utf-8')
            
        elif 'spreadsheetml.sheet' in mime_type or 'application/vnd.ms-excel' in mime_type:
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO(request.execute())
            df = pd.read_excel(fh)
            content = df.to_csv(index=False)
            
        elif 'application/pdf' in mime_type:
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
            fh.seek(0)
            pdf_reader = pypdf.PdfReader(fh)
            for page in pdf_reader.pages:
                if page.extract_text():
                    content += page.extract_text() + "\n"
                
        elif 'text/plain' in mime_type or 'text/csv' in mime_type:
            request = service.files().get_media(fileId=file_id)
            content = request.execute().decode('utf-8')
            
        else:
            return f"Found '{file_name}', but it is an unsupported format ({mime_type})."

        max_chars = 15000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... [TRUNCATED DUE TO LENGTH: Data exceeds AI memory limit.]"

        return f"✅ GOOGLE DRIVE MATCH FOUND: '{file_name}'\n\n**Document Content:**\n{content}"
    except Exception as e:
        return f"🚨 Google Drive Connection Crash: {sanitize_error_log(str(e))}"
    finally:
        try:
            del creds, service
        except NameError:
            pass

# ==========================================
# TOOL 7: PANDAS ORCHESTRATOR
# ==========================================
def hybrid_gemini_sheet_generator(instructions: str, target_sheet_name: str) -> str:
    try:
        uploaded_files = st.session_state.get("chat_uploader")
        if not uploaded_files:
            return "Error: No files currently uploaded in the Oracle Data Ingestion port. Please upload payloads first."

        df_list = []
        for uf in uploaded_files:
            file_extension = uf.name.split(".")[-1].lower()
            uf.seek(0)
            try:
                if file_extension == "csv":
                    try:
                        temp_df = pd.read_csv(uf)
                    except UnicodeDecodeError:
                        uf.seek(0)
                        temp_df = pd.read_csv(uf, encoding='cp1252', encoding_errors='replace')
                elif file_extension in ["xlsx", "xls"]:
                    temp_df = pd.read_excel(uf)
                elif file_extension == "pdf":
                    csv_string = vision_bridge_pdf_to_csv(uf)
                    if csv_string:
                        temp_df = pd.read_csv(io.StringIO(csv_string))
                    else:
                        continue
                else:
                    continue
                df_list.append(temp_df)
            except Exception as read_err:
                return f"Error reading file {uf.name}: {sanitize_error_log(str(read_err))}"

        if not df_list:
            return "Error: No valid CSV, Excel, or PDF data tables were found to combine."

        main_df = pd.concat(df_list, ignore_index=True)
        schema_info = main_df.dtypes.to_string()

        sample_df = main_df.head(3).copy()
        for col in sample_df.columns:
            if sample_df[col].dtype == 'object':
                sample_df[col] = sample_df[col].astype(str).apply(lambda x: re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '[EMAIL_MASKED]', x))
        sample_data = sample_df.to_csv(index=False)

        prompt = f"""
        You are an expert Python Pandas data architect. 
        I have a massive DataFrame `df` combining multiple raw reports.
        
        Here are the columns and their datatypes:
        {schema_info}
        
        Here is a 3-row sample of the data to understand the context:
        {sample_data}
        
        USER INSTRUCTIONS:
        {instructions}
        
        Task: Write a complete, syntactically correct Python function named `transform_df(df)` that performs all the requested filtering, renaming, calculations, and column selections.
        - The function must take a single argument `df` (the Pandas DataFrame) and return the modified `df`.
        - Handle any math natively in pandas.
        - CRITICAL DATA TYPE HANDLING: If you need to do math on a column, FORCE it to numeric first.
        - CRITICAL ROW RETENTION: DO NOT use `.dropna()`. Keep all rows.
        - ONLY output the raw Python code block inside ```python ... ```.
        """

        try:
            response_text = call_gemini_api(prompt, json_mode=False)
        except Exception as model_err:
            return f"HYBRID GEMINI CRASH: {sanitize_error_log(str(model_err))}"

        code_match = re.search(r"`{3}python(.*?)`{3}", response_text, re.DOTALL)
        if code_match:
            code_str = code_match.group(1).strip()
        else:
            code_str = response_text.replace("```", "").strip()

        local_vars = {}
        try:
            exec(code_str, {'pd': pd, 'np': np, 'datetime': datetime, 're': re}, local_vars)
            transform_df = local_vars['transform_df']
            final_df = transform_df(main_df)
        except Exception as exec_err:
            return f"Error executing Pandas transformation: {sanitize_error_log(str(exec_err))}\n\nAttempted Code:\n{sanitize_error_log(code_str)}"

        drive_scope = get_secure_endpoint("drive_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kcml2ZQ==")
        sheets_scope = get_secure_endpoint("sheets_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9zcHJlYWRzaGVldHM=")
        
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=[drive_scope, sheets_scope]
        )

        sheets_service = build("sheets", "v4", credentials=creds)
        drive_service = build("drive", "v3", credentials=creds)

        parent_folder_id = "1U8PYxUZMfJql0AYnhc0izJpI0FqveeFR"

        file_metadata = {
            'name': target_sheet_name,
            'mimeType': 'application/vnd.google-apps.spreadsheet',
            'parents': [parent_folder_id]
        }
        
        sheet_file = drive_service.files().create(
            body=file_metadata, 
            fields='id',
            supportsAllDrives=True
        ).execute()
        spreadsheet_id = sheet_file.get('id')

        headers_list = final_df.columns.tolist()
        raw_values = final_df.values.tolist()
        
        scrubbed_values = [headers_list]
        for row in raw_values:
            clean_row = []
            for item in row:
                if pd.isna(item):
                    clean_row.append("")
                else:
                    item_str = str(item)
                    if item_str.lower() in ["nan", "nat", "<na>", "none"]:
                        clean_row.append("")
                    else:
                        clean_row.append(item_str)
            scrubbed_values.append(clean_row)

        try:
            sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
            sheet_id = sheet_metadata['sheets'][0]['properties']['sheetId']
            
            requests_body = {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": sheet_id,
                                "gridProperties": {
                                    "rowCount": max(1000, len(scrubbed_values) + 100),
                                    "columnCount": max(26, len(headers_list) + 5)
                                }
                            },
                            "fields": "gridProperties(rowCount,columnCount)"
                        }
                    }
                ]
            }
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=requests_body
            ).execute()
        except Exception:
            pass

        body = { "values": scrubbed_values }
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range="Sheet1!A1",
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        human_email = st.session_state.get("user_email", "")
        if human_email:
            try:
                permission = {
                    "type": "user",
                    "role": "writer",
                    "emailAddress": human_email
                }
                drive_service.permissions().create(
                    fileId=spreadsheet_id,
                    body=permission,
                    fields="id",
                    supportsAllDrives=True
                ).execute()
            except Exception:
                pass 

        sheet_url = "[https://docs.google.com/spreadsheets/d/](https://docs.google.com/spreadsheets/d/)" + spreadsheet_id
        return f"SUCCESS: Hybrid Engine multi-file analysis complete. Title: {target_sheet_name} | URL: {sheet_url}"

    except Exception as e:
        return f"HYBRID GEMINI CRASH: {sanitize_error_log(str(e))}"
    finally:
        try:
            del creds, sheets_service, drive_service
        except NameError:
            pass

# ==========================================
# TOOL 15: WORKSPACE DOCUMENT CREATOR
# ==========================================
def tool_15_workspace_document_creator(document_title: str, document_body: str, notification_email: str = "") -> str:
    try:
        drive_scope = get_secure_endpoint("drive_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kcml2ZQ==")
        docs_scope = get_secure_endpoint("docs_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kb2N1bWVudHM=")
        
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=[drive_scope, docs_scope]
        )

        drive_service = build("drive", "v3", credentials=creds)
        docs_service = build("docs", "v1", credentials=creds)

        parent_folder_id = "1U8PYxUZMfJql0AYnhc0izJpI0FqveeFR"

        file_metadata = {
            'name': document_title,
            'mimeType': 'application/vnd.google-apps.document',
            'parents': [parent_folder_id]
        }
        
        doc_file = drive_service.files().create(
            body=file_metadata, 
            fields='id',
            supportsAllDrives=True
        ).execute()
        
        document_id = doc_file.get('id')

        if document_body:
            requests_body = {
                "requests": [
                    {
                        "insertText": {
                            "location": {
                                "index": 1,
                            },
                            "text": document_body
                        }
                    }
                ]
            }
            docs_service.documents().batchUpdate(
                documentId=document_id, 
                body=requests_body
            ).execute()

        if notification_email:
            try:
                permission = {
                    "type": "user",
                    "role": "writer",
                    "emailAddress": notification_email
                }
                drive_service.permissions().create(
                    fileId=document_id,
                    body=permission,
                    fields="id",
                    supportsAllDrives=True
                ).execute()
            except Exception:
                pass 

        doc_url = f"[https://docs.google.com/document/d/](https://docs.google.com/document/d/){document_id}"
        return f"SUCCESS: Native Google Document created in BOOF Shared Drive. Title: {document_title} | View Document: {doc_url}"

    except Exception as e:
        return f"TOOL 15 CRITICAL CRASH: {sanitize_error_log(str(e))}"
    finally:
        try:
            del creds, drive_service, docs_service
        except NameError:
            pass
