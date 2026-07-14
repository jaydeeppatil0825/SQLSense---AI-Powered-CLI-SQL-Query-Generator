import { EmptyState } from "../../components/shared/EmptyState";

export function EmptyResult() {
  return <EmptyState title="No matching records were found." message="The question ran successfully, but no rows matched." />;
}
