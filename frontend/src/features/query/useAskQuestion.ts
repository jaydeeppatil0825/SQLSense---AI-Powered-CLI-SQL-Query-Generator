import { useMutation } from "@tanstack/react-query";
import { askQuestion } from "../../gateway/actions";

export function useAskQuestion() {
  return useMutation({
    mutationFn: askQuestion,
    retry: false,
  });
}
