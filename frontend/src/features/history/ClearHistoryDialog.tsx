import { useState } from "react";
import { Button } from "../../components/ui/Button";

export function ClearHistoryDialog({ disabled, onConfirm }: { disabled: boolean; onConfirm: () => void }) {
  const [confirming, setConfirming] = useState(false);

  if (!confirming) {
    return (
      <Button disabled={disabled} onClick={() => setConfirming(true)} type="button">
        Clear History
      </Button>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-rose-400/30 bg-rose-950/20 p-3">
      <span className="text-sm text-rose-100">Clear this session history?</span>
      <Button disabled={disabled} onClick={onConfirm} type="button" variant="primary">
        Confirm clear
      </Button>
      <Button disabled={disabled} onClick={() => setConfirming(false)} type="button">
        Cancel
      </Button>
    </div>
  );
}
