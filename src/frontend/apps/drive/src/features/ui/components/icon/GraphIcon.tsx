import { IconSvg } from "./Icon";
import { IconProps } from "./Icon";

export const GraphIcon = (props: IconProps) => {
  return (
    <IconSvg {...props}>
      <path
        d="M6.5 8.5L10.2 11M13.8 11L17.5 8.5M12 13.2V16.8"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <circle cx="5" cy="7" r="2.6" stroke="currentColor" strokeWidth="1.8" />
      <circle cx="19" cy="7" r="2.6" stroke="currentColor" strokeWidth="1.8" />
      <circle cx="12" cy="11" r="3" stroke="currentColor" strokeWidth="1.8" />
      <circle cx="12" cy="19.2" r="2.6" stroke="currentColor" strokeWidth="1.8" />
    </IconSvg>
  );
};
