import { Button, Text } from "@fluentui/react-components";
import { LinkRegular } from "@fluentui/react-icons";
import { useState } from "react";

import { startFabricAuthorization } from "../../api/fabric";

type FabricAuthActionProps = {
  taskId: string;
  actionPath: string;
  start?: (taskId: string) => Promise<string>;
  navigate?: (url: string) => void;
};

export function FabricAuthAction({
  taskId,
  actionPath,
  start = startFabricAuthorization,
  navigate = (url) => {
    window.location.assign(url);
  },
}: FabricAuthActionProps) {
  const [status, setStatus] = useState<"idle" | "starting" | "failed">("idle");
  if (actionPath !== "/api/fabric/auth/start") return null;

  const connect = (): void => {
    setStatus("starting");
    void start(taskId)
      .then((authorizationUrl) => {
        sessionStorage.setItem("eda.fabric.task", taskId);
        navigate(authorizationUrl);
      })
      .catch(() => {
        setStatus("failed");
      });
  };

  return (
    <section aria-label="Fabric authorization" className="fabric-auth-action">
      <Button
        appearance="primary"
        disabled={status === "starting"}
        icon={<LinkRegular />}
        onClick={connect}
      >
        Connect to Fabric
      </Button>
      {status === "failed" && (
        <Text role="alert" size={200}>
          Connection could not start.
        </Text>
      )}
    </section>
  );
}
