import { Button, Card, Text } from "@fluentui/react-components";
import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { failed: boolean };

export class RootErrorBoundary extends Component<Props, State> {
  override state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  // React swallows the error once a boundary handles it, so this is the only place it is still readable.
  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error("workspace crashed", error, info.componentStack);
  }

  override render(): ReactNode {
    if (this.state.failed) {
      return (
        <main aria-live="assertive">
          <Card>
            <Text as="h1" size={500} weight="semibold">
              Workspace unavailable
            </Text>
            <Text>Retry the workspace or reload this page.</Text>
            <Button
              onClick={() => {
                this.setState({ failed: false });
              }}
            >
              Retry
            </Button>
            <Button
              appearance="secondary"
              onClick={() => {
                window.location.reload();
              }}
            >
              Reload
            </Button>
          </Card>
        </main>
      );
    }
    return this.props.children;
  }
}
