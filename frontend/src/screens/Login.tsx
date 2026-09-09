/** Login — a simple session sign-in. Username + password, plus one-click "sign in as"
 * buttons for the seeded demo users (passwordless while the backend is in demo mode).
 * The screen is intentionally English-only: it renders before a session exists, so the
 * interface-localization preference is not yet known. */
import { useState } from "react";

import { useDemoUsers, useLogin } from "../lib/queries";
import { color, font } from "../theme";
import type { DemoUser } from "../types";

const ROLE_BLURB: Record<string, string> = {
  admin: "Full access — configuration, template & line items, settings.",
  reviewer: "Extraction, review queue, notes, analysis and export.",
  analyst: "The simple flow — workspace, notes, analysis, export.",
};

export default function Login() {
  const { data: demo } = useDemoUsers();
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const submit = (u: string, p?: string) => {
    if (!u.trim()) return;
    login.mutate({ username: u.trim(), password: p });
  };

  const users: DemoUser[] = demo?.users ?? [];
  const demoMode = demo?.demo_mode ?? false;
  const err = login.isError ? "Invalid credentials — check the username and password." : "";

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: `linear-gradient(160deg, ${color.viewerBg} 0%, #10131a 100%)`,
        padding: 24,
        fontFamily: font.sans,
      }}
    >
      <div style={{ width: 720, maxWidth: "100%", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 0, borderRadius: 16, overflow: "hidden", boxShadow: "0 24px 60px rgba(0,0,0,.4)" }}>
        {/* Brand / left panel */}
        <div style={{ background: color.topbar, color: "#e7ebf1", padding: "40px 34px", display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 34, height: 34, borderRadius: 8, background: color.indigo, display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 16, color: "#fff" }}>FX</div>
            <span style={{ fontWeight: 600, fontSize: 18, letterSpacing: ".2px" }}>FinExtract</span>
          </div>
          <div style={{ fontSize: 13.5, color: "#aeb6c1", lineHeight: 1.6, marginTop: 4 }}>
            Financial-statement extraction with source provenance, confidence scoring,
            automated checks and a human-in-the-loop review queue.
          </div>
          <div style={{ marginTop: "auto", fontSize: 11, color: "#7f8794", lineHeight: 1.5 }}>
            Sign in to continue. Your role determines which screens and configuration you can access.
          </div>
        </div>

        {/* Form / right panel */}
        <div style={{ background: "#fff", padding: "36px 34px" }}>
          <div style={{ fontSize: 17, fontWeight: 600, color: color.ink, marginBottom: 4 }}>Sign in</div>
          <div style={{ fontSize: 12.5, color: color.muted, marginBottom: 18 }}>Access your extraction workspace.</div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit(username, password || undefined);
            }}
            style={{ display: "flex", flexDirection: "column", gap: 11 }}
          >
            <label style={{ fontSize: 11.5, fontWeight: 600, color: color.sec2 }}>
              Username
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                placeholder="admin / reviewer / analyst"
                style={inputStyle}
              />
            </label>
            <label style={{ fontSize: 11.5, fontWeight: 600, color: color.sec2 }}>
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={demoMode ? "optional in demo mode" : ""}
                style={inputStyle}
              />
            </label>
            {err && <div style={{ fontSize: 11.5, color: color.redFg }}>{err}</div>}
            <button
              type="submit"
              disabled={login.isPending || !username.trim()}
              style={{
                marginTop: 4,
                fontSize: 13,
                fontWeight: 600,
                color: "#fff",
                background: username.trim() ? color.indigo : color.controlBorder,
                border: "none",
                borderRadius: 8,
                padding: "10px 14px",
                cursor: username.trim() ? "pointer" : "default",
              }}
            >
              {login.isPending ? "Signing in…" : "Sign in"}
            </button>
          </form>

          {users.length > 0 && (
            <div style={{ marginTop: 20 }}>
              <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".5px", color: color.muted2, marginBottom: 9 }}>
                {demoMode ? "QUICK SIGN-IN (DEMO)" : "DEMO USERS"}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
                {users.map((u) => (
                  <button
                    key={u.username}
                    onClick={() => submit(u.username)}
                    disabled={login.isPending || !demoMode}
                    title={demoMode ? ROLE_BLURB[u.role] : "Enter this user's password above"}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      textAlign: "left",
                      background: "#fff",
                      border: `1px solid ${color.controlBorder}`,
                      borderRadius: 8,
                      padding: "8px 11px",
                      cursor: demoMode ? "pointer" : "default",
                      opacity: demoMode ? 1 : 0.6,
                    }}
                  >
                    <span style={{ width: 26, height: 26, flex: "0 0 26px", borderRadius: "50%", background: color.indigoTint, color: color.indigo, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 700 }}>
                      {u.name.split(" ").map((s) => s[0]).join("").slice(0, 2)}
                    </span>
                    <span style={{ minWidth: 0 }}>
                      <span style={{ display: "block", fontSize: 12.5, fontWeight: 600, color: color.ink }}>{u.name}</span>
                      <span style={{ display: "block", fontSize: 11, color: color.muted, textTransform: "capitalize" }}>{u.role}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  marginTop: 5,
  fontSize: 13,
  fontWeight: 400,
  color: "#111",
  border: `1px solid ${"#d5d9e0"}`,
  borderRadius: 8,
  padding: "9px 11px",
  outline: "none",
  boxSizing: "border-box",
};
