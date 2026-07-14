import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clearHistory, listHistory } from "../../gateway/actions";

export function useQueryHistory() {
  return useQuery({ queryKey: ["query-history"], queryFn: listHistory });
}

export function useClearHistory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: clearHistory,
    retry: false,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["query-history"] }),
  });
}
