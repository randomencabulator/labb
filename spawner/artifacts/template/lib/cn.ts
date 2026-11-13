import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// Joins class names, the later Tailwind class winning over an earlier one it conflicts with
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
