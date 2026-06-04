import requests
import base64
import re
import json
import os
import tempfile
import streamlit as st

# ==========================================
# SECURE URL RESOLVER
# ==========================================
def get_secure_endpoint(endpoint_key: str, fallback_b64: str) -> str:
    return st.secrets.get("endpoints", {}).get(endpoint_key, base64.b64decode(fallback_b64).decode())

# ==========================================
# OWASP TELEMETRY SANITISER (DSGAI14)
# ==========================================
def sanitize_error_log(error_msg: str) -> str:
    msg = str(error_msg)
    msg = re.sub(r'(?i)Bearer\s+[A-Za-z0-9\-\._~]+', 'Bearer [REDACTED_TOKEN]', msg)
    msg = re.sub(r'(?i)token=[A-Za-z0-9\-\._~]+', 'token=[REDACTED_TOKEN]', msg)
    msg = re.sub(r'(?i)api_key=[A-Za-z0-9\-\._~]+', 'api_key=[REDACTED_KEY]', msg)
    msg = re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '[EMAIL_REDACTED]', msg)
    return msg

# ==========================================
# GEMINI SDK UPGRADE WRAPPER
# ==========================================
def call_gemini_api(prompt: str, json_mode: bool = False) -> str:
    gemini_key = st.secrets.get("GEMINI_API_KEY")
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY is missing from the telemetry secrets.")
        
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=gemini_key)
        
        models = client.models.list()
        available_models = [m.name for m in models]
        
        pro_models = sorted([m for m in available_models if 'pro' in m.lower()], reverse=True)
        flash_models = sorted([m for m in available_models if 'flash' in m.lower()], reverse=True)
        
        target_model = pro_models[0] if pro_models else (flash_models[0] if flash_models else "gemini-2.5-pro")
        target_model = target_model.replace('models/', '')
        
        config_kwargs = {}
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
            
        response = client.models.generate_content(
            model=target_model, 
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        )
        try:
            return response.text.strip()
        except ValueError:
            return "[]" if json_mode else ""
        
    except ImportError:
        import google.generativeai as genai_legacy
        genai_legacy.configure(api_key=gemini_key)
        available_models = [m.name for m in genai_legacy.list_models() if 'generateContent' in m.supported_generation_methods]
        
        pro_models = sorted([m for m in available_models if 'pro' in m.lower()], reverse=True)
        flash_models = sorted([m for m in available_models if 'flash' in m.lower()], reverse=True)
        
        target_model = pro_models[0] if pro_models else (flash_models[0] if flash_models else "gemini-1.5-pro")
        target_model = target_model.replace('models/', '')
        model = genai_legacy.GenerativeModel(target_model)
        
        generation_config = genai_legacy.GenerationConfig(response_mime_type="application/json") if json_mode else None
        response = model.generate_content(prompt, generation_config=generation_config)
        try:
            return response.text.strip()
        except ValueError:
            return "[]" if json_mode else ""

# ==========================================
# VISION BRIDGE PROTOCOL
# ==========================================
def vision_bridge_pdf_to_csv(file_obj) -> str:
    gemini_key = st.secrets.get("GEMINI_API_KEY")
    if not gemini_key: 
        return ""
        
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_obj.read())
        tmp_path = tmp.name
        
    prompt = "Extract all tabular data, tables, and structured lists from this PDF. Convert the data into a strict, raw CSV format. Include column headers. Output ONLY the raw CSV text. Do not include markdown blocks or any other explanation."
    csv_text = ""
    
    try:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            models = client.models.list()
            pro_models = sorted([m.name.replace('models/', '') for m in models if 'pro' in m.name.lower()], reverse=True)
            target_model = pro_models[0] if pro_models else "gemini-2.5-pro"
            
            uploaded_file = client.files.upload(file=tmp_path, config={'mime_type': 'application/pdf'})
            response = client.models.generate_content(
                model=target_model,
                contents=[uploaded_file, prompt]
            )
            csv_text = response.text
            client.files.delete(name=uploaded_file.name)
        except ImportError:
            import google.generativeai as genai_legacy
            genai_legacy.configure(api_key=gemini_key)
            available_models = [m.name for m in genai_legacy.list_models() if 'generateContent' in m.supported_generation_methods]
            pro_models = sorted([m.replace('models/', '') for m in available_models if 'pro' in m.lower()], reverse=True)
            target_model = pro_models[0] if pro_models else "gemini-1.5-pro"
            
            uploaded_file = genai_legacy.upload_file(path=tmp_path, mime_type="application/pdf")
            model = genai_legacy.GenerativeModel(target_model)
            response = model.generate_content([uploaded_file, prompt])
            csv_text = response.text
            genai_legacy.delete_file(uploaded_file.name)
    except Exception as e:
        print(f"Vision Bridge API Failure: {sanitize_error_log(str(e))}")
    finally:
        os.remove(tmp_path)
        
    csv_text = re.sub(r"^```(csv)?\n|\n```$", "", csv_text.strip(), flags=re.IGNORECASE).strip()
    return csv_text

# ==========================================
# AUTHENTICATION CACHES
# ==========================================
@st.cache_data(ttl=1800, show_spinner=False)
def get_xero_token():
    try:
        client_id = st.secrets["xero"]["XERO_CLIENT_ID"]
        client_secret = st.secrets["xero"]["XERO_CLIENT_SECRET"]
        credentials = f"{client_id}:{client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        
        url = get_secure_endpoint("xero_auth", "aHR0cHM6Ly9pZGVudGl0eS54ZXJvLmNvbS9jb25uZWN0L3Rva2Vu")
        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = { "grant_type": "client_credentials" }
        
        response = requests.post(url, headers=headers, data=data, timeout=15)
        response.raise_for_status()
        return response.json()["access_token"]
    except Exception as e:
        return f"Error: {sanitize_error_log(str(e))}"

@st.cache_data(ttl=3000, show_spinner=False)
def get_cartoncloud_token():
    try:
        client_id = st.secrets["cartoncloud"]["client_id"].strip()
        client_secret = st.secrets["cartoncloud"]["client_secret"].strip()
        base_url = get_secure_endpoint("cartoncloud_base", "aHR0cHM6Ly9hcGkuY2FydG9uY2xvdWQuY29t")

        credentials = f"{client_id}:{client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
        auth_url = f"{base_url}/uaa/oauth/token"
        auth_headers = {
            "Accept-Version": "1",
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}"
        }
        
        response = requests.post(auth_url, data="grant_type=client_credentials", headers=auth_headers, timeout=15)
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception as e:
        return f"Error: {sanitize_error_log(str(e))}"
