#include <stdio.h>
#include <time.h>

time_t get_time_diff_in_secs()
{
	struct tm *tptr;
	time_t secs, local_secs, gmt_secs, diff_secs;

	//current time in GMT
	time(&secs);

	//remember that localtime/gmtime overwrite same location
	tptr = localtime(&secs);
	local_secs = mktime(tptr);

	tptr = gmtime(&secs);
	gmt_secs = mktime(tptr);

	diff_secs = local_secs - gmt_secs;

	return diff_secs;
}

int main(int argc, char argv[])
{
	time_t time_diff = get_time_diff_in_secs();
	printf("%ld\n", time_diff);

	return 0;
}