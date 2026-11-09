/** Spring shared with the i-am-body admin console. No React, so tests can import it. */

export const homeSpring = { type: "spring" as const, stiffness: 260, damping: 20, mass: 0.85 };
export const stagger = 0.09;

export const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0, transition: homeSpring },
};
