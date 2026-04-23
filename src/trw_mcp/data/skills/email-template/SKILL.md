---
name: email-template
description: >
  Create branded, responsive HTML email templates for transactional emails.
  Generates Jinja2 templates with table-based layout, dark mode support,
  bulletproof buttons, and plain text fallback.
  Use: /email-template "verification code" or /email-template "password reset"
user-invocable: true
argument-hint: "[email type or description]"
---

# email-template

Use when: you need a marketing or transactional email drafted against TRW brand voice and an established component template.

Generate production-ready, branded HTML email templates for the TRW platform. Templates use Jinja2 with table-based layout for maximum email client compatibility.

## Workflow

### Step 1: Parse Arguments

Determine the email type from the argument. Supported types:

| Type | Argument matches | Output file prefix |
|------|------------------|--------------------|
| verification | "verification", "verify email", "verify", "verification code" | `verify_email` |
| password_reset | "password reset", "reset password", "forgot password" | `password_reset` |
| welcome | "welcome", "onboarding" | `welcome` |
| otp_code | "otp", "otp code", "2fa code", "one-time" | `otp_code` |
| waitlist | "waitlist", "waitlist confirmation" | `waitlist_confirmation` |
| 2fa_enabled | "2fa enabled", "2fa setup", "two-factor" | `2fa_enabled` |
| custom | anything else | use argument as snake_case filename |

### Step 2: Ensure Base Template Exists

Check for `backend/templates/email/_base.html`. If it does not exist, create it with the content below.

**Base template (`_base.html`)**:

```html
<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="x-apple-disable-message-reformatting">
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  <title>{% block title %}TRW Framework{% endblock %}</title>
  <!--[if mso]>
  <noscript>
    <xml>
      <o:OfficeDocumentSettings>
        <o:AllowPNG/>
        <o:PixelsPerInch>96</o:PixelsPerInch>
      </o:OfficeDocumentSettings>
    </xml>
  </noscript>
  <![endif]-->
  <style>
    /* Reset */
    body, table, td, a { -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
    table, td { mso-table-lspace: 0pt; mso-table-rspace: 0pt; }
    img { -ms-interpolation-mode: bicubic; border: 0; height: auto; line-height: 100%; outline: none; text-decoration: none; }
    body { margin: 0; padding: 0; width: 100% !important; height: 100% !important; }

    /* Base styles */
    body {
      background-color: #F9FAFB;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      font-size: 16px;
      line-height: 1.6;
      color: #1F2937;
    }

    .email-wrapper {
      width: 100%;
      background-color: #F9FAFB;
      padding: 32px 0;
    }

    .email-content {
      max-width: 600px;
      margin: 0 auto;
    }

    .email-body {
      background-color: #FFFFFF;
      border: 1px solid #E5E7EB;
      border-radius: 8px;
      padding: 40px;
    }

    .email-header {
      text-align: center;
      padding-bottom: 24px;
      border-bottom: 1px solid #E5E7EB;
      margin-bottom: 32px;
    }

    .brand-name {
      font-size: 24px;
      font-weight: 700;
      color: #2563EB;
      text-decoration: none;
    }

    .email-footer {
      text-align: center;
      padding: 24px 0;
      color: #6B7280;
      font-size: 13px;
      line-height: 1.5;
    }

    .email-footer a {
      color: #6B7280;
      text-decoration: underline;
    }

    h1 { font-size: 24px; font-weight: 700; color: #1F2937; margin: 0 0 16px 0; }
    h2 { font-size: 20px; font-weight: 600; color: #1F2937; margin: 0 0 12px 0; }
    p { margin: 0 0 16px 0; color: #1F2937; }
    .text-muted { color: #6B7280; font-size: 14px; }
    .code-block {
      display: inline-block;
      background-color: #F3F4F6;
      border: 1px solid #E5E7EB;
      border-radius: 6px;
      padding: 12px 24px;
      font-family: 'SF Mono', Monaco, 'Courier New', monospace;
      font-size: 32px;
      font-weight: 700;
      letter-spacing: 4px;
      color: #1F2937;
    }

    /* Dark mode */
    @media (prefers-color-scheme: dark) {
      .email-wrapper { background-color: #111827 !important; }
      .email-body { background-color: #1F2937 !important; border-color: #374151 !important; }
      .email-header { border-bottom-color: #374151 !important; }
      h1, h2, p { color: #F9FAFB !important; }
      .text-muted { color: #9CA3AF !important; }
      .code-block { background-color: #374151 !important; border-color: #4B5563 !important; color: #F9FAFB !important; }
      .email-footer { color: #9CA3AF !important; }
    }
  </style>
</head>
<body>
  <!-- Preheader text (shows in inbox preview, hidden in email body) -->
  <div style="display:none;font-size:1px;line-height:1px;max-height:0px;max-width:0px;opacity:0;overflow:hidden;mso-hide:all;">
    {% block preheader %}{% endblock %}
    <!-- Pad preheader with whitespace to push ad text out of preview -->
    &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847;
  </div>

  <table role="presentation" class="email-wrapper" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr>
      <td align="center">
        <!--[if mso]>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"><tr><td>
        <![endif]-->
        <table role="presentation" class="email-content" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:600px;">
          <!-- Header -->
          <tr>
            <td>
              <div class="email-body">
                <div class="email-header">
                  <span class="brand-name">TRW Framework</span>
                </div>

                <!-- Content -->
                {% block content %}{% endblock %}
              </div>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td>
              <div class="email-footer">
                <p style="margin:0 0 4px 0;">Sent by TRW Framework</p>
                <p style="margin:0;">&copy; {{ current_year }} TRW Framework. All rights reserved.</p>
              </div>
            </td>
          </tr>
        </table>
        <!--[if mso]>
        </td></tr></table>
        <![endif]-->
      </td>
    </tr>
  </table>
</body>
</html>
```

### Step 3: Generate the Specific Template

Create `backend/templates/email/{type}.html` extending `_base.html`.

**Design tokens (use consistently)**:
- Primary: `#2563EB` (blue)
- Background: `#F9FAFB`
- Text: `#1F2937`
- Muted text: `#6B7280`
- Border: `#E5E7EB`
- Font stack: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif`

**Template structure**:
```html
{% extends "_base.html" %}

{% block title %}Subject Line Here{% endblock %}

{% block preheader %}Inbox preview text here{% endblock %}

{% block content %}
<h1>Heading</h1>
<p>Body text with {{ template_variable }}.</p>

<!-- CTA Button (if needed) -->
<!-- Use the bulletproof button pattern below -->

<p class="text-muted">Footer note, e.g. expiry information.</p>
{% endblock %}
```

**Bulletproof button pattern** (use for all CTA buttons):

```html
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin: 24px auto;">
  <tr>
    <td align="center" bgcolor="#2563EB" style="border-radius: 6px;">
      <!--[if mso]>
      <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="{{ action_url }}" style="height:44px;width:200px;v-text-anchor:middle;" arcsize="14%" fillcolor="#2563EB" stroke="f">
        <v:textbox inset="0,0,0,0"><center style="color:#ffffff;font-family:Arial,sans-serif;font-size:16px;font-weight:bold;">{{ button_text }}</center></v:textbox>
      </v:roundrect>
      <![endif]-->
      <!--[if !mso]><!-->
      <a href="{{ action_url }}" style="display:inline-block;padding:12px 32px;background-color:#2563EB;color:#ffffff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:16px;font-weight:bold;text-decoration:none;border-radius:6px;">{{ button_text }}</a>
      <!--<![endif]-->
    </td>
  </tr>
</table>
```

### Step 4: Generate Plain Text Version

Create `backend/templates/email/{type}.txt` with the same content in plain text format.

**Plain text conventions**:
- Use `===` or `---` for section separators
- Replace buttons with: `Action: {{ action_url }}`
- Replace code blocks with the code value directly
- Keep the same template variables as the HTML version
- Include footer: `-- Sent by TRW Framework`

**Example plain text template**:
```
Hi {{ user_name }},

Your verification code is: {{ code }}

This code expires in {{ expiry_minutes }} minutes.

If you did not request this, you can safely ignore this email.

--
Sent by TRW Framework
```

### Step 5: Output Summary

After creating the templates, output a summary:

```
Created files:
  - backend/templates/email/_base.html (if newly created)
  - backend/templates/email/{type}.html
  - backend/templates/email/{type}.txt

Template variables:
  - {{ var1 }} -- description
  - {{ var2 }} -- description

Next steps:
  - Register template in backend/services/email.py send function
  - Add template name to the EmailTemplate enum (if applicable)
  - Write a test in backend/tests/test_email.py for template rendering
```

## Email Types Reference

| Type | File prefix | Variables | Has button |
|------|-------------|-----------|------------|
| verification | `verify_email` | `user_name`, `action_url`, `expiry_minutes` | Yes -- "Verify Email" |
| password_reset | `password_reset` | `user_name`, `action_url`, `expiry_minutes` | Yes -- "Reset Password" |
| welcome | `welcome` | `user_name`, `frontend_url` | Yes -- "Get Started" |
| otp_code | `otp_code` | `user_name`, `code`, `expiry_minutes` | No -- uses code block |
| waitlist | `waitlist_confirmation` | `user_name` | No |
| 2fa_enabled | `2fa_enabled` | `user_name`, `frontend_url` | Yes -- "Manage Settings" |

## Template Variable Conventions

- `{{ user_name }}` -- recipient's display name (or "there" if unknown)
- `{{ action_url }}` -- primary CTA link (full URL)
- `{{ code }}` -- OTP or verification code (displayed in code block)
- `{{ expiry_minutes }}` -- how long until link or code expires
- `{{ frontend_url }}` -- base URL for the platform (e.g. https://trwframework.com)
- `{{ current_year }}` -- current year for copyright (provided by render function)

## Guidelines

1. **Always extend `_base.html`** -- never write standalone HTML email templates.
2. **Use table-based layout** -- divs are unreliable across email clients.
3. **Inline critical styles** -- premailer handles this at send time, but inline `style=""` attributes on key elements as a safety net.
4. **Keep content concise** -- emails should be scannable in under 10 seconds.
5. **Always include plain text** -- some recipients prefer or require plain text.
6. **Test with Litmus or Email on Acid** -- rendering varies wildly across clients.
7. **No JavaScript** -- email clients strip all JS.
8. **No external CSS** -- most clients strip `<link>` tags.
9. **Images are optional** -- the template must be fully functional without images loading.
10. **Dark mode support** -- the base template includes `prefers-color-scheme: dark` overrides; specific templates inherit this automatically.
