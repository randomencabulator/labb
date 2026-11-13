// Joins the class names that are set
export const cx = (...names: (string | false | null | undefined)[]) => names.filter(Boolean).join(" ");

// Base UI takes a class name or a function of the state; ours take a plain one
export type Styled<P> = Omit<P, "className"> & { className?: string };
