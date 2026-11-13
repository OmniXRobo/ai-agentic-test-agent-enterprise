// First line of defence before a chat message leaves the page: something that looks like a password, one-time code,
// MFA or driver code is not sent at all. The agent checks again on its side (skyguard_agent.secrets_guard) and never
// stores, logs or forwards such a message. Shared by SkyGuard and the browser extension.
const WORDS = "pass(?:word|wd|code|phrase)?|pwd|pin|otp|one[\\s-]?time(?:\\s+(?:code|password|pin))?|verification\\s+code|auth(?:entication)?\\s+code|security\\s+code|2fa(?:\\s+code)?|mfa(?:\\s+code)?|totp|recovery\\s+code|backup\\s+code|driver\\s+(?:auth(?:entication)?\\s+)?code|access\\s+code";
const LABELLED = new RegExp(`\\b(${WORDS})\\b\\s*(is|was|=|:|-)?\\s*["'\`]?([^\\s"'\`,;]{3,})`, "gi");
const CODE_WORD = /^(?:otp|one[\s-]?time|verification|auth|security|2fa|mfa|totp|access|driver|recovery|backup)/i;
const CODE_IS = /\b(?:code|pin)\b\s*(?:is|=|:)\s*\d{4,10}\b/i;
const HARMLESS = new Set(["field", "fields", "box", "reset", "change", "expired", "policy", "rules", "screen", "page", "form", "required", "manager", "vault", "for", "of", "and", "or", "the", "a", "an", "to", "with", "please", "entry", "input", "dialog", "button", "label", "manually", "later", "securely", "store", "stored", "saved", "save", "capture", "is", "are", "be", "not", "no", "yes", "account", "accounts", "login", "credentials", "credential", "missing", "wrong", "incorrect", "invalid", "failed", "needed", "authentication", "process", "step", "stage", "enter", "provide", "ask", "type", "should", "would", "will", "can", "cannot", "must", "number", "it", "this", "that", "when", "if", "in", "on", "here"]);

export function looksSecret(text) {
  if (!text) return false;
  if (CODE_IS.test(text)) return true;
  for (const m of text.matchAll(LABELLED)) {
    const word = m[1].toLowerCase(), sep = m[2], value = m[3].replace(/[.!?]+$/, "");
    if (HARMLESS.has(value.toLowerCase())) continue;
    if (word === "pin" && !/\d/.test(value)) continue;
    if (CODE_WORD.test(word) && !sep && !/\d/.test(value)) continue; // "the OTP Operator account": a name
    return true;
  }
  return false;
}

export const SECRET_WARNING = "That message looks like it contains a password or a one-time code, so it was not sent. " +
  "Use Accounts > Add / Replace credentials for passwords. One-time and driver codes are typed directly into the application when it asks.";
