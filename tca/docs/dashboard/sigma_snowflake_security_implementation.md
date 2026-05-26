# Sigma + Snowflake Security Implementation Guide

This document provides code examples for implementing secure client-facing dashboards using Sigma Computing with Snowflake Gold layer.

---

## Deployment Architecture: Where Each Component Runs

```
┌──────────┐         HTTPS         ┌──────────────────┐        HTTPS       ┌────────────┐
│ BROWSER  │ ───────────────────── │ BANK WEB SERVER  │ ────────────────── │   SIGMA    │
│          │                       │                  │                    │            │
│ 1. Loads │   (auth only)         │ - /auth/login    │                    │ 4. Serves  │
│   dashboard.html                 │ - /auth/callback │                    │    iframe  │
│          │                       │ - /api/sigma/    │                    │            │
│ 2. Calls │ ──────────────────►   │   embed-config   │                    │            │
│   embed  │                       │   (returns URL)  │                    │            │
│   config │                       └──────────────────┘                    └─────┬──────┘
│          │                                                                     │
│          │         3. Sigma embed URL                                          │
│          │ ◄───────────────────────────────────────────────────────────────────┘
│          │
│          │                                            5. Connects directly to Snowflake
│          │                                            (NO callback to Bank's server)
│          │
│          │                                            ┌───────────────┘
│          │                                            │ SQL with client_role param
│          │                                            ▼
│          │                                    ┌────────────┐
│          │                                    │ SNOWFLAKE  │
│          │                                    │            │
│          │                                    │ - Filters  │
│          │                                    │   by role  │
│          │                                    └────────────┘
```

### Data Flow Explanation

| Step | From → To | What Happens |
|------|-----------|--------------|
| 1 | Browser → Bank's Web Server | Load `dashboard.html` (requires authentication) |
| 2 | Browser → Bank's Web Server | Call `/api/sigma/embed-config` to get Sigma embed URL |
| 3 | Bank's Web Server → Browser | Returns signed Sigma embed URL with `client_id`, `client_role` |
| 4 | Browser → Sigma | Embed Sigma iframe using the URL |
| 5 | Sigma → Snowflake | **Direct SQL connection** — Sigma connects with the role from the URL |
| **Note** | | **Sigma never calls bank's web server** after step 3 |

### Component Summary

| Component | File | Runs On | Purpose |
|-----------|------|---------|---------|
| **SigmaAuth class** | `sigma_saml_config.py` | Bank's web server (Flask/FastAPI) | Handles OAuth2 with Okta |
| **login() endpoint** | `sigma_saml_config.py` | Bank's web server | Initiates OAuth2 flow |
| **oauth_callback() endpoint** | `sigma_saml_config.py` | Bank's web server | Exchanges code for tokens |
| **get_embed_config() endpoint** | `sigma_embed_service.py` | Bank's web server | Generates signed Sigma embed URL |
| **client-dashboard.html** | client-dashboard.html | Bank's web server (served to browser) | Renders Sigma iframe |
| **Snowflake network policy** | network_policy.sql | Snowflake | Restricts IPs |
| **Row access policies** | row_access_policies.sql | Snowflake | Filters data per client |
| **Client roles** | client_roles.sql | Snowflake | Per-tenant access control |

**Important:** The embed URL contains all parameters Sigma needs (`client_id`, `client_role`). Sigma uses these to connect to Snowflake directly — there is no callback to Bank's server during dashboard usage.

---

## 1. OAuth2 Authentication Flow (Client Browser → Sigma)

### Two Approaches for Client Authentication

When embedding Sigma in a bank client portal, you have two options for how authentication works:

| Aspect | Option A: Sigma-Managed Auth | Option B: Bank's Own Auth (Recommended for Banks) |
|--------|------------------------------|------------------------------------------------|
| **Who handles login** | Sigma (via Okta SAML/OIDC configured in Sigma admin) | Bank's portal (custom `/auth/login`, `/auth/callback`) |
| **Login page** | Sigma's login page embedded in Bank's portal | Bank's bank's portal login page |
| **Code required** | Minimal — configure Okta in Sigma UI | More — implement OAuth2 flow Bank'sself |
| **Parameter passing** | Limited control | Full control — signed parameters |
| **Session control** | Sigma controls session | You control session entirely |
| **Flexibility** | Lower | Higher |

#### Option A: Sigma-Managed Auth (Less Code)

```
Client visits portal → Embedded Sigma iframe → Sigma shows Okta login → 
Client logs into Okta → Okta redirects to Sigma → Dashboard loads
```

You configure Okta directly in Sigma's admin UI (Settings → Authentication → SAML/OIDC).
Users log in via Sigma's interface, not Bank's portal.

#### Option B: Bank's Own Auth (More Control)

This is the approach detailed in this document:

```
Client clicks "Login" in Bank's portal → Bank's /auth/login redirects to Okta → 
Client logs into Okta → Okta redirects to /auth/callback → Bank's app creates session → 
Bank's app generates signed Sigma embed URL → Dashboard loads in iframe
```

**Why Option B is recommended for banks:**
- Users stay in Bank's portal — consistent brand experience
- You control the session (can add MFA, IP checks, session timeout)
- Client parameters are cryptographically signed (prevents tampering)
- Full audit trail through Bank's own logs
- Can enforce additional security policies

---

### Identity Provider Setup (Okta Example)

> **Note:** The following code implements **Option B** — Bank's own auth infrastructure.

```python
# Configuration for SAML/OAuth2 via Okta
# File: sigma_saml_config.py

import requests
from urllib.parse import urlencode
from flask import Flask, redirect, request, session, jsonify
from functools import wraps

app = Flask(__name__)
app.secret_key = "store-in-env-var"  # For session management

# In production, store these in a secure secret manager
# In the current implementation, SigmaAuth is a simplified example. In production, typically use a dedicated #library like python-jose or Authlib, or rely on Bank's existing auth infrastructure (e.g. existing SSO #gateway). The class would be instantiated in Bank's auth route handler, not embedded in the client-side code.

#Okta is an enterprise Identity and Access Management (IAM) platform — essentially a service that handles user #authentication and authorization.

#In this context, Okta provides:
#Single Sign-On (SSO) — Users log in once through Okta to access multiple applications
#User management — Stores client credentials, handles password policies, MFA
#OAuth2/SAML — Standard protocols for secure authentication
#It's one of the leading IdP (Identity Provider) solutions, along with Microsoft Entra ID (formerly Azure AD), PingIdentity, and Auth0.

In our dashboard flow: Clients authenticate with Okta (their credentials are never seen by Bank's app), and Okta sends back a token proving they logged in successfully.


OKTA_DOMAIN = "Bank's-bank.okta.com"
OKTA_CLIENT_ID = "Bank's-okta-client-id"
OKTA_CLIENT_SECRET = "Bank's-okta-client-secret"
SIGMA_REDIRECT_URI = "https://Bank's-bank.sigmacomputing.com/oauth/callback"

#When a client clicks "Login", SigmaAuth builds the redirect to Okta. After the client logs in, SigmaAuth exchanges the returned code for tokens that create their session.
class SigmaAuth:
    """
    Handles OAuth2 authentication flow between client browser and Bank's IdP (Okta).
    
    Purpose:
    - Initiates the OAuth2 authorization code flow when client clicks "Login"
    - Exchanges authorization code for access tokens after IdP redirects back
    - Returns tokens that the backend uses to create a session for the client
    
    This class is NOT called from the browser - it's used by the backend auth routes.
    """
    
    def __init__(self, tenant_id: str = None, client_id: str = None):
        self.tenant_id = tenant_id or OKTA_CLIENT_ID
        self.client_id = client_id or OKTA_CLIENT_ID
        self.idp_metadata_url = f"https://{OKTA_DOMAIN}/app/abc123/sso/saml/metadata"
        self.sigma_redirect_uri = SIGMA_REDIRECT_URI
    
    def initiate_oauth_flow(self) -> str:
        """Generate OAuth2 authorization URL for client login"""
        state = self._generate_state_token()
        # Store state in session for CSRF validation
        session['oauth_state'] = state
        
        auth_params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.sigma_redirect_uri,
            "scope": "openid profile email",
            "state": state,
            "audience": "https://api.sigmacomputing.com"
        }
        return f"https://{OKTA_DOMAIN}/oauth2/v1/authorize?{urlencode(auth_params)}"
    
    def exchange_code_for_token(self, authorization_code: str) -> dict:
        """Exchange authorization code for access token"""
        token_url = f"https://{OKTA_DOMAIN}/oauth2/v1/token"
        
        response = requests.post(token_url, data={
            "grant_type": "authorization_code",
            "code": authorization_code,
            "client_id": self.client_id,
            "client_secret": OKTA_CLIENT_SECRET,
            "redirect_uri": self.sigma_redirect_uri
        })
        
        if response.status_code != 200:
            raise Exception(f"Token exchange failed: {response.text}")
        
        return response.json()  # Contains access_token, id_token, refresh_token
    
    def _generate_state_token(self) -> str:
        import secrets
        return secrets.token_urlsafe(32)


# ============================================================================
# AUTH ROUTES - These are the actual endpoints that use SigmaAuth
# ============================================================================

# Instantiate the SigmaAuth helper (used by the endpoints below)
sigma_auth = SigmaAuth()


@app.route("/auth/login")
def login():
    """
    Step 1: Client clicks "Login" button on dashboard
    
    This endpoint:
    - Generates OAuth2 authorization URL
    - Redirects client to Okta login page
    """
    auth_url = sigma_auth.initiate_oauth_flow()
    return redirect(auth_url)


@app.route("/auth/callback")
def oauth_callback():
    """
    Step 2: Okta redirects back with authorization code
    
    This endpoint:
    - Receives the authorization code from Okta
    - Validates the state parameter (CSRF protection)
    - Exchanges code for access token
    - Creates a session for the client
    - Redirects to dashboard
    """
    # Validate state parameter to prevent CSRF attacks
    expected_state = session.get('oauth_state')
    actual_state = request.args.get('state')
    
    if not expected_state or expected_state != actual_state:
        return jsonify({"error": "Invalid state parameter"}), 400
    
    # Clear state from session
    session.pop('oauth_state', None)
    
    # Get authorization code
    code = request.args.get('code')
    if not code:
        return jsonify({"error": "No authorization code provided"}), 400
    
    try:
        # Exchange code for tokens using SigmaAuth
        tokens = sigma_auth.exchange_code_for_token(code)
        
        # Store tokens in session (or better, use secure httpOnly cookies)
        session['access_token'] = tokens['access_token']
        session['id_token'] = tokens['id_token']
        
        # Decode ID token to get client_id claim
        # In production, use proper  (Jason Web Token) decoding
        import json
        import base64
        id_token_payload = json.loads(
            base64.b64decode(tokens['id_token'].split('.')[1] + '==')
        )
        session['client_id'] = id_token_payload.get('client_id')
        
        return redirect("/dashboard")
        
    except Exception as e:
        return jsonify({"error": f"Authentication failed: {str(e)}"}), 500


@app.route("/auth/logout")
def logout():
    """Clear session and redirect to login"""
    session.clear()
    return redirect("/auth/login")


# Decorator to require authentication
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'access_token' not in session:
            return redirect("/auth/login")
        return f(*args, **kwargs)
    return decorated_function
```

---

### How SigmaAuth Fits Into the Authentication Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        USER CLICKS "LOGIN"                              │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  GET /auth/login                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ sigma_auth.initiate_oauth_flow()  ←── Instantiates SigmaAuth    │   │
│  │                                                                  │   │
│  │ 1. Generates random state token                                 │   │
│  │ 2. Builds OAuth2 authorization URL                              │   │
│  │ 3. Stores state in session                                      │   │
│  │ 4. Returns redirect to Okta login page                          │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    USER LOGS IN TO OKTA                                 │
│         (Enters username/password - not seen by our app)               │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  GET /auth/callback?code=xxx&state=yyy   ←─ Okta redirects back        │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ 1. Validate state (CSRF check)                                   │   │
│  │ 2. sigma_auth.exchange_code_for_token(code)                      │   │
│  │                                                                  │   │
│  │    This calls:                                                   │   │
│  │    - Okta token endpoint                                         │   │
│  │    - Exchanges authorization code                                │   │
│  │    - Returns: access_token, id_token, refresh_token              │   │
│  │                                                                  │   │
│  │ 3. Decode id_token to extract client_id                         │   │
│  │ 4. Store tokens in session                                       │   │
│  │ 5. Redirect to /dashboard                                        │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  USER ACCESSES DASHBOARD                                │
│  Session now contains: access_token, client_id                         │
│  Subsequent requests use session to authenticate                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

**In summary:** `SigmaAuth` is a helper class used by Bank's backend's `/auth/login` and `/auth/callback` endpoints to orchestrate the OAuth2 flow with Bank's bank's IdP. It's not called from the browser — it's a server-side utility that handles the redirect to Okta and the token exchange.

### Sigma Embed SDK Integration

```html
<!-- Client-facing embed page -->
<!-- File: client-dashboard.html -->
<!-- 
  This file runs in the CLIENT BROWSER.
  It is served by your bank's web server (Flask/FastAPI).
  
  Flow:
  1. Browser loads this page (user already authenticated via session)
  2. initDashboard() calls your backend to get Sigma embed URL
  3. Browser embeds Sigma iframe using the returned URL
  4. Sigma connects directly to Snowflake (no further calls to your server)
-->

<!DOCTYPE html>
<html>
<head>
    <title>Client Portal - Transaction Dashboard</title>
    <script src="https://cdn.sigmacomputing.com/embed/v2/sigma.embed.js"></script>
    <style>
        body { margin: 0; padding: 20px; font-family: Arial, sans-serif; }
        #sigma-dashboard { width: 100%; height: 90vh; border: none; }
        .loading { text-align: center; padding: 50px; }
    </style>
</head>
<body>
    <!-- Sigma dashboard container -->
    <div id="sigma-dashboard">
        <div class="loading">Loading dashboard...</div>
    </div>
    
    <script>
        // ========================================================================
        // HELPER: Get access token from session
        // ========================================================================
        async function getAccessToken() {
            // The session is set by /auth/callback after Okta login
            // In production, this would come from an httpOnly cookie or secure storage
            const token = sessionStorage.getItem('access_token');
            if (!token) {
                // Redirect to login if no session
                window.location.href = '/auth/login';
                throw new Error('Not authenticated');
            }
            return token;
        }
        
        // ========================================================================
        // HELPER: Parse JWT to extract client_id
        // ========================================================================
        function parseJwt(token) {
            try {
                const base64Url = token.split('.')[1];
                const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
                const jsonPayload = decodeURIComponent(
                    atob(base64).split('').map(function(c) {
                        return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
                    }).join('')
                );
                return JSON.parse(jsonPayload);
            } catch (e) {
                console.error('Failed to parse JWT:', e);
                return null;
            }
        }
        
        // ========================================================================
        // HELPER: Get client_id from session
        // ========================================================================
        function getClientIdFromSession() {
            const jwt = parseJwt(sessionStorage.getItem('access_token'));
            if (!jwt || !jwt['client_id']) {
                throw new Error('No client_id in token');
            }
            return jwt['client_id'];
        }
        
        // ========================================================================
        // MAIN: Initialize Sigma dashboard
        // ========================================================================
        async function initDashboard() {
            try {
                // Step 1: Get access token
                const accessToken = await getAccessToken();
                
                // Step 2: Get client_id from JWT
                const clientId = getClientIdFromSession();
                
                // Step 3: Call your backend to get Sigma embed URL
                // This is the ONLY call to your server during dashboard load
                const response = await fetch('/api/sigma/embed-config', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${accessToken}`,
                        'X-Client-ID': clientId
                    },
                    body: JSON.stringify({
                        dashboard_id: 'transactions',
                        client_id: clientId
                    })
                });
                
                if (!response.ok) {
                    throw new Error(`Failed to get embed config: ${response.status}`);
                }
                
                const embedConfig = await response.json();
                
                // Step 4: Initialize Sigma embed
                SigmaEmbed.init(
                    document.getElementById('sigma-dashboard'),
                    {
                        embedUrl: embedConfig.embedUrl,
                        parameters: {
                            client_id: embedConfig.clientId,
                            client_role: embedConfig.clientRole,
                            session_id: embedConfig.sessionId
                        },
                        hideParameters: true,      // Prevent parameter tampering
                        hideExport: true,          // Disable data export
                        hideFilters: false,        // Allow filtering
                        width: '100%',
                        height: '90vh'
                    }
                );
                
            } catch (error) {
                console.error('Dashboard initialization failed:', error);
                document.getElementById('sigma-dashboard').innerHTML = 
                    `<div class="loading">Error loading dashboard. Please <a href="/auth/login">login</a> again.</div>`;
            }
        }
        
        // Initialize when page loads
        document.addEventListener('DOMContentLoaded', initDashboard);
    </script>
</body>
</html>
```

---

### How This Page Works

| Step | What Happens |
|------|--------------|
| 1 | Browser loads `dashboard.html` — user already has session from `/auth/callback` |
| 2 | `initDashboard()` runs — calls `/api/sigma/embed-config` with access token |
| 3 | Your backend validates token, generates signed Sigma embed URL |
| 4 | Browser receives embed URL and passes it to `SigmaEmbed.init()` |
| 5 | Sigma iframe loads — connects directly to Snowflake |

**Key security features in this file:**
- `hideParameters: true` — prevents users from tampering with client_id/role
- `hideExport: true` — disables data export to prevent data leakage
- Access token passed in Authorization header — backend validates session

---

## 2. Security Filters Per User Session

### Backend: Sigma Embed Configuration API

```python
# File: sigma_embed_service.py
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
import sigma
import os

app = FastAPI()

# In production, load from secure secret manager
SIGMA_API_KEY = os.environ.get("SIGMA_API_KEY")
SIGMA_ORG_ID = os.environ.get("SIGMA_ORG_ID")

# Counterparty role mappings (stored in database)
# Maps counterparty_id from JWT to their corresponding Snowflake role
# This matches the counterparty_role_mapping table in Snowflake
COUNTERPARTY_ROLE_MAPPING = {
    "ABC": {
        "snowflake_role": "CLIENT_ABC_READER",  # Role that can see counterparty ABC data
        "allowed_dashboards": ["tca_analysis", "client_activity"]
    },
    "XYZ": {
        "snowflake_role": "CLIENT_XYZ_READER", 
        "allowed_dashboards": ["tca_analysis", "client_activity"]
    },
    "DEF": {
        "snowflake_role": "CLIENT_DEF_READER", 
        "allowed_dashboards": ["tca_analysis", "client_activity"]
    }
}

class EmbedConfigRequest(BaseModel):
    dashboard_id: str
    client_id: str

def verify_client_access(token: str, client_id: str) -> dict:
    """Verify JWT and ensure client can only access their own data"""
    # Verify JWT signature, expiration, issuer
    # Extract client_id from token and verify it matches request
    # This prevents token theft attacks
    pass

@app.post("/api/sigma/embed-config")
async def get_embed_config(
    request: EmbedConfigRequest,
    token: str = Depends(lambda: get_token_from_header())
):
    # Verify authentication and authorization
    client_info = verify_client_access(token, request.client_id)
    
    # Get Sigma embed URL
    sigma_client = sigma.Client(api_key=SIGMA_API_KEY, org_id=SIGMA_ORG_ID)
    
    # Generate signed embed URL with client-specific parameters
    # These parameters are cryptographically signed and cannot be tampered
    embed_url = sigma_client.get_embed_url(
        workbook_id=request.dashboard_id,
        parameters={
            "client_id": client_info["client_id"],      # Maps to Snowflake role
            "client_role": client_info["snowflake_role"],
            "session_id": client_info["session_id"],     # For audit logging
        },
        # Force parameter isolation - client cannot override
        parameters_signature=sigma_client.sign_parameters(
            {"client_id": client_info["client_id"]}
        )
    )
    
    return {
        "embedUrl": embed_url,
        "clientId": client_info["client_id"],
        "clientRole": client_info["snowflake_role"]
    }
```

### Sigma Workbook Configuration

```sql
-- In Sigma, create workbook with client-scoped queries
-- The :client_id parameter is injected by Sigma from embed config

-- Example: Sigma SQL worksheet
SELECT 
    transaction_date,
    amount,
    currency,
    transaction_type,
    balance
FROM snowflake.gold.transactions
WHERE client_id = :client_id
  AND transaction_date >= CURRENT_DATE - 365
ORDER BY transaction_date DESC
```

---

## 3. Snowflake Network Policy

```sql
-- Only allow Sigma server IPs to connect to Snowflake
-- File: network_policy.sql

-- Step 1: Create network policy
CREATE NETWORK POLICY sigma_dashboard_policy
    ALLOWED_IP_LIST = (
        '52.35.178.1/32',   # Sigma US East
        '52.35.178.2/32',   # Sigma US East HA
        '52.199.1.1/32',    # Sigma EU (Frankfurt)
        '52.199.1.2/32',    # Sigma EU HA
        '13.234.1.1/32',    # Sigma APAC (Mumbai)
        '13.234.1.2/32'     # Sigma APAC HA
    )
    BLOCKED_IP_LIST = ()
    COMMENT = 'Sigma Computing dashboard servers - review quarterly';

-- Step 2: Apply to account
ALTER ACCOUNT SET NETWORK_POLICY = sigma_dashboard_policy;

-- Step 3: Verify
SELECT * FROM SNOWFLAKE.INFORMATION_SCHEMA.NETWORK_POLICIES;

-- Step 4: Add Bank's internal admin IPs for emergency access
ALTER NETWORK POLICY sigma_dashboard_policy
    SET ALLOWED_IP_LIST = (
        '52.35.178.1/32',
        '52.35.178.2/32',
        '52.199.1.1/32',
        '52.199.1.2/32',
        '13.234.1.1/32',
        '13.234.1.2/32',
        'Bank's_BANK_OFFICE_IP/32',    -- Emergency admin access
        'Bank's_VPN_IP/32'             -- Admin VPN
    );

-- Step 5: Get Sigma IP ranges (run this periodically - Sigma publishes them)
-- https://help.sigmacomputing.com/hc/en-us/articles/4405190251411-IP-Addresses-and-DNS-Names
```

---

## 4. Row Access Policy at Database Level

```sql
-- File: row_access_policies.sql
-- CRITICAL: This is enforced at Snowflake, not Sigma

-- Step 1: Create counterparty-to-role mapping table
-- Maps counterparty_id (from fact tables) to Snowflake role
CREATE TABLE security.counterparty_role_mapping (
    counterparty_id VARCHAR(50) PRIMARY KEY,  -- Matches counterparty_id in fact tables
    snowflake_role_name VARCHAR(100) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);

-- Insert counterparty role mappings
-- This maps your actual counterparty IDs to their corresponding roles
INSERT INTO security.counterparty_role_mapping (counterparty_id, snowflake_role_name) VALUES
    ('ABC', 'CLIENT_ABC_READER'),
    ('XYZ', 'CLIENT_XYZ_READER'),
    ('DEF', 'CLIENT_DEF_READER');

-- Step 2: Create row access policy using counterparty_id
CREATE OR REPLACE ROW ACCESS POLICY rap_counterparty_isolation
AS (counterparty_id_param VARCHAR)
RETURNS BOOLEAN
AS (
    -- Check if current role is allowed to see this counterparty's data
    EXISTS (
        SELECT 1 FROM security.counterparty_role_mapping m
        WHERE m.counterparty_id = counterparty_id_param
          AND m.snowflake_role_name = CURRENT_ROLE()
          AND m.is_active = TRUE
    )
    OR
    -- Allow internal analytics roles full access
    CURRENT_ROLE() IN ('ANALYTICS_ADMIN', 'SECURITY_AUDITOR')
);

-- Step 3: Apply to fact tables using counterparty_id column
-- Note: In your TCA project, the client identifier column is 'counterparty_id'
ALTER TABLE {{ ref('fact_order_execution') }} ADD ROW ACCESS POLICY rap_counterparty_isolation 
    ON (counterparty_id);

ALTER TABLE {{ ref('fact_client_activity') }} ADD ROW ACCESS POLICY rap_counterparty_isolation 
    ON (counterparty_id);

-- Also apply to any other tables that contain counterparty_id
-- ALTER TABLE ... ADD ROW ACCESS POLICY rap_counterparty_isolation ON (counterparty_id);

-- Example
-- User's Role	Query Result
-- CLIENT_ABC_READER	Only rows where counterparty_id = 'ABC'
-- CLIENT_XYZ_READER	Only rows where counterparty_id = 'XYZ'
-- ANALYTICS_ADMIN	All rows (bypasses policy)
-- Key point: The user cannot bypass this — it's enforced at the database level, not in the application.

-- Step 4: Verify policy is active
SELECT 
    table_name,
    policy_name,
    policy_kind
FROM SNOWFLAKE.INFORMATION_SCHEMA.APPLIED_ROW_ACCESS_POLICIES
WHERE table_schema IN ('CORPORATE', 'TRADING_RISK');

-- Step 5: Test isolation (as different roles)
-- Test as ABC counterparty
USE ROLE CLIENT_ABC_READER;
SELECT COUNT(*) FROM {{ ref('fact_order_execution') }};  -- Returns only ABC data

-- Test as XYZ counterparty  
USE ROLE CLIENT_XYZ_READER;
SELECT COUNT(*) FROM {{ ref('fact_order_execution') }};  -- Returns only XYZ data
```

---

### Key Difference: `client_id` vs `counterparty_id`

| Generic Example | Your TCA Project |
|---------------- |------------------|
| `client_id`         | `counterparty_id` |
| `gold.transactions` | `fact_order_execution`, `fact_client_activity` |
| Row access policy filters by `client_id` | Row access policy filters by `counterparty_id` |

The row access policy logic is the same — it just uses your actual column name. The policy checks if the user's Snowflake role matches the `counterparty_id` they're trying to query.

---

## 5. Separate Reader Role Per Client Tenant

```sql
-- File: client_roles.sql
-- Note: Using your TCA project schema names (corporate, trading_risk)

-- Step 1: Create base role for clients
CREATE ROLE CLIENT_GENERIC_READER;

-- Step 2: Create individual counterparty roles
-- These map to the counterparty_id values in your data
CREATE ROLE CLIENT_ABC_READER;   -- For counterparty_id = 'ABC'
CREATE ROLE CLIENT_XYZ_READER;   -- For counterparty_id = 'XYZ'
CREATE ROLE CLIENT_DEF_READER;   -- For counterparty_id = 'DEF'

-- Step 3: Grant SELECT on your marts schemas to base role
GRANT USAGE ON DATABASE {{ target.database }} TO ROLE CLIENT_GENERIC_READER;
GRANT USAGE ON SCHEMA {{ target.database }}.CORPORATE TO ROLE CLIENT_GENERIC_READER;
GRANT USAGE ON SCHEMA {{ target.database }}.TRADING_RISK TO ROLE CLIENT_GENERIC_READER;

-- Grant SELECT on fact tables
GRANT SELECT ON {{ ref('fact_order_execution') }} TO ROLE CLIENT_GENERIC_READER;
GRANT SELECT ON {{ ref('fact_client_activity') }} TO ROLE CLIENT_GENERIC_READER;
GRANT SELECT ON {{ ref('dim_client') }} TO ROLE CLIENT_GENERIC_READER;

-- Step 4: Inherit base permissions
GRANT ROLE CLIENT_GENERIC_READER TO ROLE CLIENT_ABC_READER;
GRANT ROLE CLIENT_GENERIC_READER TO ROLE CLIENT_XYZ_READER;
GRANT ROLE CLIENT_GENERIC_READER TO ROLE CLIENT_DEF_READER;

-- Step 5: Grant roles to Sigma service account
-- The Sigma connection will SET ROLE based on client context
CREATE USER SIGMA_SERVICE_ACCOUNT
    PASSWORD = 'use_strong_generated_password'
    DEFAULT_ROLE = CLIENT_GENERIC_READER
    MUST_CHANGE_PASSWORD = FALSE;

CREATE ROLE SIGMA_SERVICE_ROLE;
GRANT ROLE SIGMA_SERVICE_ROLE TO USER SIGMA_SERVICE_ACCOUNT;

-- Sigma can switch to client-specific roles via session parameter
GRANT ROLE CLIENT_ABC_READER TO ROLE SIGMA_SERVICE_ROLE;
GRANT ROLE CLIENT_XYZ_READER TO ROLE SIGMA_SERVICE_ROLE;
GRANT ROLE CLIENT_DEF_READER TO ROLE SIGMA_SERVICE_ROLE;

-- Step 6: Allow role switching (controlled via Sigma embed parameters)
GRANT IMPERSONATE ON ROLE CLIENT_ABC_READER TO ROLE SIGMA_SERVICE_ROLE;
GRANT IMPERSONATE ON ROLE CLIENT_XYZ_READER TO ROLE SIGMA_SERVICE_ROLE;
GRANT IMPERSONATE ON ROLE CLIENT_DEF_READER TO ROLE SIGMA_SERVICE_ROLE;

-- Step 7: Restrict what Sigma service account CANNOT do
GRANT USAGE ON DATABASE {{ target.database }} TO ROLE CLIENT_GENERIC_READER;

-- Step 8: Create specific user for each counterparty (optional, for audit)
CREATE USER CLIENT_ABC_USER
    PASSWORD = 'use_strong_generated_password'
    DEFAULT_ROLE = CLIENT_ABC_READER
    COMMENT = 'Counterparty ABC dashboard access - managed by Sigma SSO';

GRANT ROLE CLIENT_ABC_READER TO USER CLIENT_ABC_USER;
```

---

## 6. Complete Sigma Connection Configuration

```yaml
# Sigma connection configuration (in Sigma UI or API)
# File: sigma_snowflake_connection.yaml

connection:
  name: "Snowflake Gold Layer - Secure"
  type: "snowflake"
  
  # Service account credentials (stored in Sigma, never exposed)
  account: "Bank's-bank.us-east-1"
  warehouse: "ANALYTICS_WH"
  database: "GOLD"
  schema: "PUBLIC"
  
  # Authentication
  auth_method: "user_password"  # Uses SIGMA_SERVICE_ACCOUNT
  
  # Role management - Sigma will switch to client role
  role: "{{ parameters.client_role }}"  # Parameterized!
  
  # Security settings
  query_tag: "CLIENT_DASHBOARD"
  enable_query_result_cache: false  # Don't cache sensitive data
  
  # Timeout settings
  connection_timeout: 30
  query_timeout: 120
```

---

## 7. End-to-End Flow Summary

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. CLIENT LOGIN (Browser)                                               │
│    └─> Bank IdP (Okta) via SAML/OAuth2                                  │
│         └─> Returns JWT with client_id claim (tamper-proof (signed),    │
│          self-contained (has all needed info))                          │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. DASHBOARD REQUEST (Browser → Bank's Backend)                         │
│    POST /api/sigma/embed-config                                         │
│    Headers: Authorization: Bearer <JWT>                                 │
│    Body: { dashboard_id: "transactions", client_id: "ABC" }             │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. BACKEND VALIDATION                                                   │
│    - Verify JWT signature                                               │
│    - Verify client_id matches JWT claim                                 │
│    - Lookup client role (CLIENT_ABC_READER)                             │
│    - Generate signed Sigma embed URL with parameters                    │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. SIGMA EMBED (Browser)                                                │
│    - Load dashboard in iframe                                           │
│    - Sigma receives parameters: client_id=ABC, client_role=ABC_READER   │
│    - Sigma connects to Snowflake with role=CLIENT_ABC_READER            │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 5. SNOWFLAKE QUERY                                                      │
│    - Network policy: Only Sigma IPs allowed ✓                           │
│    - Row access policy: Returns only client_id=ABC rows ✓               │
│    - Role: CLIENT_ABC_READER (cannot see XYZ data) ✓                    │
│    - Query tagged: CLIENT_DASHBOARD for audit                          │ 
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 8. Security Checklist
- [ ] Network policy restricts Snowflake to Sigma IPs only
- [ ] Row access policies on all gold tables
- [ ] Separate role per client tenant
- [ ] OAuth2/SAML for client authentication
- [ ] JWT validation on backend before generating embed URL
- [ ] Parameter signing to prevent tampering
- [ ] Query tagging enabled for audit
- [ ] No direct Snowflake credentials exposed to clients
- [ ] Dynamic data masking on PII columns