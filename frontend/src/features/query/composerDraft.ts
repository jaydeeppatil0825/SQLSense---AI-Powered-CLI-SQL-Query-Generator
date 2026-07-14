let draft: string | null = null;

export function setQuestionDraft(question: string) {
  draft = question;
}

export function takeQuestionDraft(): string {
  const value = draft ?? "";
  draft = null;
  return value;
}
