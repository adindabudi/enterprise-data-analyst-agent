import { Button, Text } from "@fluentui/react-components";
import {
  CheckmarkCircleRegular,
  LinkRegular,
  WarningRegular,
} from "@fluentui/react-icons";
import { useState } from "react";

import {
  startFabricAuthorization,
  type FabricAuthorizationStatus,
} from "../../api/fabric";

type FabricAvailability = "disabled" | "failed" | "configured" | "ready";

type FabricConnectionActionProps = {
  availability: FabricAvailability;
  authorization?: FabricAuthorizationStatus;
  start?: () => Promise<string>;
  navigate?: (url: string) => void;
};

export function FabricConnectionAction({
  availability,
  authorization,
  start = () => startFabricAuthorization(),
  navigate = (url) => {
    window.location.assign(url);
  },
}: FabricConnectionActionProps) {
  const [starting, setStarting] = useState(false);
  const [failed, setFailed] = useState(false);

  if (availability === "disabled") return null;
  if (availability === "failed" || authorization === undefined || failed) {
    return (
      <span className="fabric-connection fabric-connection--unavailable">
        <WarningRegular aria-hidden="true" />
        <Text size={200}>Fabric unavailable</Text>
      </span>
    );
  }
  if (authorization.state === "linked") {
    // A ready source answers everywhere; a configured one still answers in chat only.
    if (availability === "ready") {
      return (
        <span className="fabric-connection fabric-connection--linked">
          <CheckmarkCircleRegular aria-hidden="true" />
          <Text size={200}>Fabric connected</Text>
        </span>
      );
    }
    return authorization.chatQuery ? (
      <span className="fabric-connection fabric-connection--linked">
        <CheckmarkCircleRegular aria-hidden="true" />
        <Text size={200}>Fabric connected for chat</Text>
      </span>
    ) : (
      <span className="fabric-connection fabric-connection--pending">
        <WarningRegular aria-hidden="true" />
        <Text size={200}>Fabric not ready</Text>
      </span>
    );
  }

  const label =
    authorization.state === "reauth_required"
      ? "Reconnect Fabric"
      : "Connect Fabric";
  const connect = (): void => {
    setStarting(true);
    setFailed(false);
    void start()
      .then(navigate)
      .catch(() => {
        setFailed(true);
        setStarting(false);
      });
  };

  return (
    <Button
      appearance="subtle"
      className="fabric-connection fabric-connection--action"
      disabled={starting}
      icon={<LinkRegular />}
      onClick={connect}
      size="small"
    >
      {starting ? "Connecting" : label}
    </Button>
  );
}
