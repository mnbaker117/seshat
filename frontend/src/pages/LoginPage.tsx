// LoginPage handles both first-run admin setup and the regular
// login flow. The mode is driven by the `firstRun` prop, which the
// /api/auth/check call resolves before this component renders.
import { useState, type FormEvent } from "react";
import { Btn } from "../components/Btn";
import { Spin } from "../components/Spin";
import { api, ApiError } from "../api";
import { useTheme } from "../theme";

interface Props {
  firstRun: boolean;
  onLoginSuccess: () => void;
}

export default function LoginPage({ firstRun, onLoginSuccess }: Props) {
  const theme = useTheme();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (firstRun) {
      if (password.length < 8) {
        setError("Password must be at least 8 characters.");
        return;
      }
      if (password !== confirm) {
        setError("Passwords do not match.");
        return;
      }
    }
    setBusy(true);
    try {
      const path = firstRun ? "/auth/setup" : "/auth/login";
      await api.post(path, { username, password });
      onLoginSuccess();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: theme.bg,
        padding: 20,
      }}
    >
      <form
        onSubmit={submit}
        style={{
          background: theme.bg2,
          border: `1px solid ${theme.border}`,
          borderRadius: 14,
          padding: 32,
          width: "100%",
          maxWidth: 380,
          animation: "slide-up 0.25s ease-out",
        }}
      >
        <h1
          style={{
            fontSize: 22,
            fontWeight: 700,
            color: theme.accent,
            marginBottom: 4,
          }}
        >
          Seshat
        </h1>
        <p
          style={{
            fontSize: 13,
            color: theme.textDim,
            marginBottom: 24,
          }}
        >
          {firstRun
            ? "First-run setup — create your admin account."
            : "Sign in to continue."}
        </p>

        <Field
          label="Username"
          value={username}
          onChange={setUsername}
          autoFocus
          autoComplete="username"
        />
        <Field
          label="Password"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete={firstRun ? "new-password" : "current-password"}
        />
        {firstRun && (
          <Field
            label="Confirm password"
            type="password"
            value={confirm}
            onChange={setConfirm}
            autoComplete="new-password"
          />
        )}

        {error && (
          <div
            style={{
              background: theme.err + "22",
              border: `1px solid ${theme.err}55`,
              color: theme.err,
              padding: "10px 12px",
              borderRadius: 8,
              fontSize: 13,
              marginTop: 12,
            }}
          >
            {error}
          </div>
        )}

        <div style={{ marginTop: 20 }}>
          <Btn
            type="submit"
            variant="primary"
            disabled={busy || !username || !password}
            fullWidth
          >
            {busy ? <Spin size={16} /> : firstRun ? "Create account" : "Sign in"}
          </Btn>
        </div>
      </form>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  autoFocus,
  autoComplete,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  autoFocus?: boolean;
  autoComplete?: string;
}) {
  const theme = useTheme();
  return (
    <label
      style={{
        display: "block",
        marginBottom: 12,
        fontSize: 12,
        color: theme.textDim,
        fontWeight: 600,
      }}
    >
      {label}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        type={type}
        autoFocus={autoFocus}
        autoComplete={autoComplete}
        style={{
          display: "block",
          width: "100%",
          marginTop: 6,
          padding: "10px 12px",
          borderRadius: 8,
          border: `1px solid ${theme.border}`,
          background: theme.bg3,
          color: theme.text,
          fontSize: 14,
          outline: "none",
        }}
      />
    </label>
  );
}
