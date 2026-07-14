import { PageHeader } from "../../components/shared/PageHeader";
import { Card } from "../../components/ui/Card";

export function SettingsPage() {
  return (
    <>
      <PageHeader
        eyebrow="Preferences"
        title="Preferences"
        description="Adjust local interface preferences."
      />
      <Card>
        <h2 className="text-lg font-bold text-white">Business workspace preferences</h2>
        <p className="mt-2 text-sm leading-6 text-slate-300">
          Theme controls are available in the header. More preferences can be added when needed.
        </p>
      </Card>
    </>
  );
}
