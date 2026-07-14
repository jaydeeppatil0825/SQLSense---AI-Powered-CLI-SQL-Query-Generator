import { paths } from "../router/paths";

export type NavItem = {
  label: string;
  description: string;
  to: string;
};

export const NAV_ITEMS: NavItem[] = [
  { label: "Home", description: "Business data workspace", to: paths.app },
  { label: "Ask Your Data", description: "Question workspace", to: paths.query },
  { label: "Query History", description: "Past validated answers", to: paths.history },
  { label: "Data Connection", description: "Connect company data", to: paths.database },
  { label: "Data Knowledge", description: "Prepare data meaning", to: paths.knowledgeBase },
  { label: "System Status", description: "Service diagnostics", to: paths.systemStatus },
  { label: "Preferences", description: "Interface settings", to: paths.settings },
];
