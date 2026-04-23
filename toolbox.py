import requests
import base64
import streamlit as st

# ==========================================
# XERO CONNECTION HANDSHAKE
# ==========================================
def get_xero_token():
    """Silently logs into Xero using the vault credentials to get a temporary access token."""
    client_id = st.secrets["XERO_CLIENT_ID"]
    client_secret = st.secrets["XERO_CLIENT_SECRET"]
    
    # Xero requires the ID and Secret to be mashed together and encoded
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    
    url = "https://identity.xero.com/connect/token"
    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "client_credentials"
    }
    
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        return f"Error connecting to Xero: {response.text}"

# ==========================================
# TOOL 1: XERO CONTACT SEARCH
# ==========================================
def search_xero_contact(contact_name):
    """Searches Xero for a specific contact name to see if they exist and are active."""
    token = get_xero_token()
    
    if "Error" in token:
        return token # Return the error if handshake failed
        
    url = f'https://api.xero.com/api.xro/2.0/Contacts?where=Name.Contains("{contact_name}")'
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        if data.get("Contacts"):
            contact = data["Contacts"][0]
            return f"Found Contact: {contact.get('Name')}. Status: {contact.get('ContactStatus')}."
        else:
            return f"No contact found in Xero matching '{contact_name}'."
    else:
        return f"Xero API Error: {response.text}"

# ==========================================
# TOOL 2: MACHSHIP CONNOTE SEARCH
# ==========================================
def search_machship_connote(connote_number):
    """Searches Machship for a consignment number to get carrier and status."""
    token = st.secrets["MACHSHIP_API_TOKEN"]
    
    # Machship API endpoint for searching consignments
    url = f"https://live.machship.com/apiv2/consignments/returnConsignment?consignmentNumber={connote_number}"
    headers = {
        "token": token,
        "Accept": "application/json"
    }
    
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        if data.get("object"):
            consignment = data["object"]
            carrier = consignment.get("carrier", {}).get("name", "Unknown Carrier")
            status = consignment.get("status", {}).get("name", "Unknown Status")
            return f"Machship Record - Carrier: {carrier}, Status: {status}."
        else:
            return f"Could not find consignment {connote_number} in Machship."
    else:
        return f"Machship API Error: {response.text}"