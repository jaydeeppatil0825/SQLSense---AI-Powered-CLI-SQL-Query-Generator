export type ResultRow = Record<string, unknown>;

export type ChartType = "bar" | "line" | "kpi";

export type ChartColumn = {
  name: string;
  kind: "text" | "number" | "date";
};

export type ChartOption = {
  type: ChartType;
  label: string;
  categoryColumn?: string;
  dateColumn?: string;
  valueColumn: string;
};

export type ChartEligibility = {
  eligible: ChartOption[];
  textColumns: ChartColumn[];
  numericColumns: ChartColumn[];
  dateColumns: ChartColumn[];
  unavailableReason: string | null;
};
