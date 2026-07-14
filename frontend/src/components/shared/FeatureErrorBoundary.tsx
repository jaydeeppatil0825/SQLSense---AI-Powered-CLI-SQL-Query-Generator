import { Component, type ErrorInfo, type ReactNode } from "react";
import { GatewayErrorPanel } from "./GatewayErrorPanel";

type Props = { children: ReactNode; title?: string };
type State = { failed: boolean };

export class FeatureErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // Keep details out of the UI; devtools still has the source error.
  }

  render() {
    if (this.state.failed) {
      return <GatewayErrorPanel title={this.props.title ?? "Page unavailable"} message="This part of SQLSense could not render safely." />;
    }
    return this.props.children;
  }
}
