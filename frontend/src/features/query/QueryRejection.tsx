import { GatewayErrorPanel } from "../../components/shared/GatewayErrorPanel";
import { clarificationOptions } from "./queryMessages";
import { ClarificationOptions } from "./ClarificationOptions";

export function QueryRejection({
  code,
  message,
  details,
  onClarify,
}: {
  code: string;
  message: string;
  details?: unknown;
  onClarify: (text: string) => void;
}) {
  return (
    <div>
      <GatewayErrorPanel title={message} message={`Reference code: ${code}`} />
      <ClarificationOptions options={clarificationOptions(details)} onSelect={onClarify} />
    </div>
  );
}
