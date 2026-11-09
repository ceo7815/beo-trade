"use client";

import { type ReactNode } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { homeSpring, itemVariants, stagger } from "@/lib/iambodyMotion";

export { homeSpring, itemVariants, stagger };

export function MotionStage({ children, className }: { children: ReactNode; className?: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={reduce ? false : "hidden"}
      animate="show"
      variants={{
        hidden: {},
        show: { transition: { staggerChildren: reduce ? 0 : stagger, delayChildren: reduce ? 0 : 0.04 } },
      }}
    >
      {children}
    </motion.div>
  );
}

export function Rise({ children, className = "" }: { children: ReactNode; className?: string }) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;
  return (
    <motion.div className={className} variants={itemVariants} transition={homeSpring}>
      {children}
    </motion.div>
  );
}
