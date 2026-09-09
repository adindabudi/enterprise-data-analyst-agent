import {
  Button,
  FluentProvider,
  Spinner,
  Text,
} from "@fluentui/react-components";
import { useEffect, useState } from "react";

import { ApiClient, ApiRequestError } from "./api/client";
import {
  completeFabricAuthorization,
  getFabricAuthorizationStatus,
  type FabricAuthorizationStatus,
} from "./api/fabric";
import { DEFAULT_BRANDING } from "./app/branding";
import { compileTheme } from "./app/theme";
import { FabricConnectionAction } from "./features/fabric/FabricConnectionAction";
import type { FabricAvailability } from "./features/fabric/source-context";
import { DesktopWorkspace } from "./layout/DesktopWorkspace";

type BootstrapState = "booting" | "signed_out" | "authenticated" | "failed";
const api = new ApiClient();

export function App() {
  const [state, setState] = useState<BootstrapState>("booting");
  const [fabricAvailability, setFabricAvailability] =
    useState<FabricAvailability>("disabled");
  const [fabricAuthorization, setFabricAuthorization] =
    useState<FabricAuthorizationStatus>();
  const [isMobile, setIsMobile] = useState(
    () => window.matchMedia("(max-width: 1023px)").matches,
  );
  const retry = (): void => {
    setState("booting");
  };

  useEffect(() => {
    const media = window.matchMedia("(max-width: 1023px)");
    const update = (event: MediaQueryListEvent): void => {
      setIsMobile(event.matches);
    };
    media.addEventListener("change", update);
    return () => {
      media.removeEventListener("change", update);
    };
  }, []);

  useEffect(() => {
    if (state !== "booting") return;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      controller.abort();
    }, 10000);
    void api
      .get<{ status: "authenticated" }>("/api/auth/session", controller.signal)
      .then(async () => {
        if (window.location.pathname === "/fabric-auth/complete") {
          await completeFabricAuthorization();
          const taskId = sessionStorage.getItem("eda.fabric.task");
          sessionStorage.removeItem("eda.fabric.task");
          const destination =
            taskId && /^task_[A-Za-z0-9_-]{8,}$/.test(taskId)
              ? `/?task=${encodeURIComponent(taskId)}`
              : "/";
          window.history.replaceState(null, "", destination);
        }
        try {
          type Readiness = {
            featurePacks?: { fabric?: unknown };
          };
          // A blocked pack answers 503 but still reports which packs are healthy.
          const readiness = await api
            .get<Readiness>("/health/ready", controller.signal)
            .catch((error: unknown) =>
              error instanceof ApiRequestError && error.body !== undefined
                ? (error.body as Readiness)
                : Promise.reject(
                    error instanceof Error ? error : new Error(String(error)),
                  ),
            );
          const fabric = readiness.featurePacks?.fabric;
          const availability: FabricAvailability = [
            "disabled",
            "failed",
            "configured",
            "ready",
          ].includes(String(fabric))
            ? (fabric as FabricAvailability)
            : "failed";
          setFabricAvailability(availability);
          if (availability === "configured" || availability === "ready") {
            try {
              setFabricAuthorization(await getFabricAuthorizationStatus());
            } catch {
              setFabricAuthorization(undefined);
            }
          } else {
            setFabricAuthorization(undefined);
          }
        } catch {
          setFabricAvailability("failed");
          setFabricAuthorization(undefined);
        }
        setState("authenticated");
      })
      .catch((error: unknown) => {
        setState(
          error instanceof ApiRequestError && error.status === 401
            ? "signed_out"
            : "failed",
        );
      })
      .finally(() => {
        window.clearTimeout(timeout);
      });
    return () => {
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [state]);

  return (
    <FluentProvider theme={compileTheme(DEFAULT_BRANDING)}>
      <div className="app-shell">
        <a className="skip-link" href="#workspace-main">
          Skip to main content
        </a>
        <header className="app-product-bar">
          <div className="app-product-bar__identity">
            <span aria-hidden="true" className="product-mark">
              <span />
              <span />
              <span />
            </span>
            <Text as="h1" className="product-name" size={400} weight="semibold">
              {DEFAULT_BRANDING.productName}
            </Text>
          </div>
          <div className="app-product-bar__actions">
            {state === "authenticated" && (
              <FabricConnectionAction
                availability={fabricAvailability}
                {...(fabricAuthorization === undefined
                  ? {}
                  : { authorization: fabricAuthorization })}
              />
            )}
            <div className="workspace-privacy">
              <span aria-hidden="true" className="workspace-privacy__signal" />
              <Text size={200} weight="medium">
                Private workspace
              </Text>
            </div>
          </div>
        </header>
        {state === "authenticated" ? (
          <DesktopWorkspace
            compact={isMobile}
            fabricAvailability={fabricAvailability}
            {...(fabricAuthorization === undefined
              ? {}
              : { fabricAuthorization })}
          />
        ) : (
          <main className="bootstrap-state" id="workspace-main" tabIndex={-1}>
            {state === "booting" && (
              <Spinner label="Preparing your session" labelPosition="after" />
            )}
            {state === "signed_out" && (
              <Button
                onClick={() => {
                  window.location.href = "/api/auth/login";
                }}
              >
                Sign In
              </Button>
            )}
            {state === "failed" && (
              <section role="alert">
                <Text>Workspace could not connect.</Text>
                <Button onClick={retry}>Retry</Button>
              </section>
            )}
          </main>
        )}
      </div>
    </FluentProvider>
  );
}
