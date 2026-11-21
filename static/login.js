"use strict";
// Sign-in for password mode. The session is an HttpOnly cookie; this page never stores the password.
document.getElementById("login").onsubmit = async e => {
  e.preventDefault();
  const err = document.getElementById("err"), btn = e.target.querySelector("button");
  err.hidden = true; btn.disabled = true;
  try {
    const r = await fetch("/api/login", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Food": "1" },
      body: JSON.stringify({ password: document.getElementById("pw").value }) });
    if (r.ok) return location.replace("/");
    const j = await r.json().catch(() => ({}));
    err.textContent = j.error || `HTTP ${r.status}`; err.hidden = false;
  } catch (x) {
    err.textContent = x.message; err.hidden = false;
  }
  btn.disabled = false;
};
