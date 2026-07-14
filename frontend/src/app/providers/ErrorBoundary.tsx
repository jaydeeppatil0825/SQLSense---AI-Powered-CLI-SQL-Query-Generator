import { Component, type ErrorInfo, type ReactNode } from "react";
import { GatewayErrorPanel } from "../../components/shared/GatewayErrorPanel";

type Props = { children: ReactNode };
type State = { hasError: boolean };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // Keep transport output safe; detailed traces stay in devtools only.
  }

  render() {
    if (this.state.hasError) {
      return <GatewayErrorPanel title="Application error" message="The interface could not render safely." />;
    }
    return this.props.children;
  }
}
