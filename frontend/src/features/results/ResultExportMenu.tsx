import { Button } from "../../components/ui/Button";
import type { QueryResult } from "../../gateway/contracts";
import { downloadText, exportCsv, exportJson, safeResultFilename } from "./exportResult";

export function ResultExportMenu({ result }: { result: QueryResult }) {
  const exportResult = (format: "csv" | "json") => {
    const contents = format === "csv" ? exportCsv(result.columns, result.rows) : exportJson(result.columns, result.rows);
    const type = format === "csv" ? "text/csv;charset=utf-8" : "application/json;charset=utf-8";
    downloadText(safeResultFilename(format), contents, type);
  };

  return (
    <div className="flex flex-wrap gap-2">
      <Button type="button" onClick={() => exportResult("csv")}>
        Export CSV
      </Button>
      <Button type="button" onClick={() => exportResult("json")}>
        Export JSON
      </Button>
    </div>
  );
}
