"use client";

import { FormEvent, Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

type RegistrationState = "checking" | "ready" | "submitting" | "failed";

type InvitationInspectionResponse = {
  email: string;
};

function isInvitationInspectionResponse(value: unknown): value is InvitationInspectionResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "email" in value &&
    typeof value.email === "string"
  );
}

function RegisterPageContent() {
  const searchParams = useSearchParams();
  // Preserve the token for this page instance after removing it from the URL.
  const [invitationToken] = useState(() => searchParams.get("token"));
  
  const [state, setState] = useState<RegistrationState>("checking");
  const [token, setToken] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (invitationToken === null) {
      return;
    }

    const controller = new AbortController();

    void (async () => {
      try {
        const response = await fetch("/api/auth/invitations/inspect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: invitationToken }),
          cache: "no-store",
          signal: controller.signal,
        });

        const payload: unknown = await response.json();

        if (!response.ok || !isInvitationInspectionResponse(payload)) {
          setState("failed");
          setError("This invitation is invalid, expired, or already used.");
          return;
        }

        setToken(invitationToken);
        setEmail(payload.email);

        // Remove the bearer token from the visible URL and browser history.
        window.history.replaceState(null, "", "/register");
        setState("ready");
      } catch {
        if (!controller.signal.aborted) {
          setState("failed");
          setError("Unable to validate the invitation. Please try again later.");
        }
      }
    })();

    return () => controller.abort();
  }, [invitationToken]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (token === null) {
      setState("failed");
      setError("The invitation is no longer available. Please reopen the invitation link.");
      return;
    }

    const form = new FormData(event.currentTarget);
    const password = form.get("password");
    const confirmation = form.get("passwordConfirmation");

    if (typeof password !== "string" || typeof confirmation !== "string") {
      setState("failed");
      setError("Please enter and confirm your password.");
      return;
    }

    if (password !== confirmation) {
      setState("failed");
      setError("The passwords do not match.");
      return;
    }

    setState("submitting");
    setError(null);

    try {
      const response = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
        cache: "no-store",
      });

      if (!response.ok) {
        setState("failed");
        setError("Registration could not be completed. The invitation may have expired.");
        return;
      }

      // The BFF has already written the HttpOnly session cookie.
      window.location.replace("/");
    } catch {
      setState("failed");
      setError("Unable to complete registration. Please try again later.");
    }
  }

  if (invitationToken === null) {
    return (
      <main className="grid min-h-screen place-items-center bg-zinc-100 px-4 py-8">
        <section className="w-full max-w-md border border-zinc-200 bg-white p-6 sm:p-8">
          <p className="text-sm font-medium text-zinc-500">AgentOS</p>
          <h1 className="mt-2 text-xl font-semibold text-zinc-950">Create your account</h1>
          <p role="alert" className="mt-4 text-sm text-rose-700">
            Please use the registration link sent by an administrator.
          </p>
        </section>
      </main>
    );
  }

  return (
    <main className="grid min-h-screen place-items-center bg-zinc-100 px-4 py-8">
      <section className="w-full max-w-md border border-zinc-200 bg-white p-6 sm:p-8">
        <p className="text-sm font-medium text-zinc-500">AgentOS</p>
        <h1 className="mt-2 text-xl font-semibold text-zinc-950">Create your account</h1>

        {state === "checking" ? (
          <p className="mt-4 text-sm text-zinc-600">Validating invitation...</p>
        ) : null}

        {state !== "checking" && email !== null ? (
          <form onSubmit={(event) => void submit(event)}>
            <label className="mt-6 block text-sm font-medium text-zinc-800">
              Email
              <input
                readOnly
                value={email}
                className="mt-2 w-full cursor-not-allowed border border-zinc-300 bg-zinc-100 px-3 py-2 text-zinc-600"
              />
            </label>

            <label className="mt-4 block text-sm font-medium text-zinc-800">
              Password
              <input
                required
                minLength={8}
                name="password"
                type="password"
                autoComplete="new-password"
                className="mt-2 w-full border border-zinc-300 px-3 py-2"
              />
            </label>

            <label className="mt-4 block text-sm font-medium text-zinc-800">
              Confirm password
              <input
                required
                minLength={8}
                name="passwordConfirmation"
                type="password"
                autoComplete="new-password"
                className="mt-2 w-full border border-zinc-300 px-3 py-2"
              />
            </label>

            {error ? <p role="alert" className="mt-4 text-sm text-rose-700">{error}</p> : null}

            <button
              type="submit"
              disabled={state === "submitting"}
              className="mt-6 w-full bg-zinc-950 px-4 py-2.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {state === "submitting" ? "Creating account..." : "Create account"}
            </button>
          </form>
        ) : null}

        {state === "failed" && email === null ? (
          <p role="alert" className="mt-4 text-sm text-rose-700">{error}</p>
        ) : null}
      </section>
    </main>
  );
}

export default function RegisterPage() {
  return (
    <Suspense fallback={<main className="min-h-screen bg-zinc-100" aria-busy="true" />}>
      <RegisterPageContent />
    </Suspense>
  );
}