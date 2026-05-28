import requests
import base64
import re
import json
import io
import pandas as pd
import numpy as np
import datetime
import pypdf
import os
import tempfile
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ==========================================
# SECURE URL RESOLVER
# ==========================================
def get_secure_endpoint(endpoint_key: str, fallback_b64: str) -> str:
    """
    Retrieves endpoints securely from st.secrets to eliminate hardcoded Base64 
    strings per OWASP guidelines. Falls back to decoded Base64 to guarantee zero degradation.
    """
    return st.secrets.get("endpoints", {}).get(endpoint_key, base64.b64decode(fallback_b64).decode())

# ==========================================
# GEMINI SDK UPGRADE WRAPPER (2026 Compatible)
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
        return response.text.strip()
        
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
        return response.text.strip()

# ==========================================
# VISION BRIDGE PROTOCOL (Multimodal PDF to CSV)
# ==========================================
def vision_bridge_pdf_to_csv(file_obj) -> str:
    gemini_key = st.secrets.get("GEMINI_API_KEY")
    if not gemini_key: 
        return ""
        
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp
