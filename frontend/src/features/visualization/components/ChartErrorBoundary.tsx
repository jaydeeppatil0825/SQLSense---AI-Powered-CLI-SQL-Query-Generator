import { Component, type ReactNode } from "react";
import { ChartUnavailable } from "./ChartUnavailable";

export class ChartErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return <ChartUnavailable reason="This chart could not be displayed. The table is still available." />;
    }
    return this.props.children;
  }
}
