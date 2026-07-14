import { Component, type ReactNode } from "react";
import { GatewayErrorPanel } from "../../components/shared/GatewayErrorPanel";

export class ResultErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return <GatewayErrorPanel title="Results unavailable" message="The result could not be displayed safely." />;
    }
    return this.props.children;
  }
}
